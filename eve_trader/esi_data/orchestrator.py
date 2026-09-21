"""ESI fetch orchestrator: one task per owner, kinds sequential inside it.

Sharing decides *what* to refresh; the token selector picks `auth_role`
for a given (character, required scope). Group 3 is not an orchestrator
kind. This module imports no tool package.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from .. import storage
from ..access_gate import ALL_TOOL_KEYS
from ..auth import TokenManager, TokenRecord
from ..config import OAUTH_CONFIG, TRADING_CONFIG
from ..esi_client import ESIClient, ESIError
from .fetchers import fetcher_for
from .registry import OWNED_DATA_KINDS, TIER_FREQUENT, TIER_NORMAL, TIER_RARE
from .selector import REAUTH_NEEDED, select_auth_role
from .stale import clear_stale_owner_kind

log = logging.getLogger(__name__)

# Dataclass defaults on TradingConfig.esi_*_interval_hours. _tier_hours
# reads the live config; this dict is the documented fallback matching
# those defaults (frequent 1h / normal 6h / rare 24h).
DEFAULT_TIER_INTERVAL_HOURS = {
    TIER_FREQUENT: 1.0,
    TIER_NORMAL: 6.0,
    TIER_RARE: 24.0,
}

_KIND_TIER = {k.key: k.freshness_tier for k in OWNED_DATA_KINDS}
_KIND_BY_KEY = {k.key: k for k in OWNED_DATA_KINDS}

# Per-owner in-process guard (decision 7): a manual tool sync and the
# scheduler must not process the same owner concurrently.
_guard_mu = threading.Lock()
_in_flight: set[tuple] = set()


def _owner_guard_key(owner_type: str, owner_id: int) -> tuple:
    return (storage.get_current_tenant(), owner_type, owner_id)


def _try_begin_owner(owner_type: str, owner_id: int) -> bool:
    key = _owner_guard_key(owner_type, owner_id)
    with _guard_mu:
        if key in _in_flight:
            return False
        _in_flight.add(key)
        return True


def _end_owner(owner_type: str, owner_id: int) -> None:
    key = _owner_guard_key(owner_type, owner_id)
    with _guard_mu:
        _in_flight.discard(key)


def _tier_hours(data_kind: str) -> float:
    cfg = TRADING_CONFIG
    by_tier = {
        TIER_FREQUENT: cfg.esi_frequent_interval_hours,
        TIER_NORMAL: cfg.esi_normal_interval_hours,
        TIER_RARE: cfg.esi_rare_interval_hours,
    }
    return by_tier[_KIND_TIER[data_kind]]


def _stale_clear_multiples() -> float:
    return TRADING_CONFIG.esi_stale_clear_multiples


def _hours_since_success(iso_or_dt, now: Optional[datetime] = None) -> float:
    if iso_or_dt is None:
        return float("inf")
    if isinstance(iso_or_dt, str):
        since = datetime.fromisoformat(iso_or_dt.replace("Z", "+00:00"))
    else:
        since = iso_or_dt
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return (clock - since).total_seconds() / 3600.0


def _kind_is_due(
    owner_type: str, owner_id: int, data_kind: str, *, now: Optional[datetime] = None,
) -> bool:
    last = storage.get_esi_freshness_success_at(owner_type, owner_id, data_kind)
    return _hours_since_success(last, now) >= _tier_hours(data_kind)


def _required_scope(data_kind: str, owner_type: str) -> Optional[str]:
    spec = _KIND_BY_KEY.get(data_kind)
    if spec is None:
        return None
    if owner_type == "character":
        return spec.character_scope
    return spec.corporation_scope


def _list_known_characters(tm: TokenManager) -> list[TokenRecord]:
    """Unique characters this tenant has any token for, first-seen order.

    Not a prefix listing: every stored row counts, whatever its key.
    """
    seen: set[int] = set()
    out: list[TokenRecord] = []
    for rec in tm.list_records():
        if rec.character_id in seen:
            continue
        seen.add(rec.character_id)
        out.append(rec)
    return out


def _display_name(
    owner_type: str, owner_id: int, characters: list[TokenRecord],
    corp_names: dict[int, str],
) -> str:
    if owner_type == "character":
        for rec in characters:
            if rec.character_id == owner_id:
                return rec.character_name or str(owner_id)
        return str(owner_id)
    name = corp_names.get(owner_id) or str(owner_id)
    return f"{name} (corp)"


def _run_kind(
    *,
    client: ESIClient,
    data_kind: str,
    owner_type: str,
    owner_id: int,
    auth_role: str,
    owner_name: str,
    extra: dict,
) -> dict:
    fn = fetcher_for(data_kind, owner_type)
    return fn(client, owner_id, auth_role, owner_name, **extra)


def _record_success(owner_type: str, owner_id: int, data_kind: str) -> None:
    storage.upsert_esi_freshness(owner_type, owner_id, data_kind, success=True)


def _record_failure(owner_type: str, owner_id: int, data_kind: str, error: BaseException) -> None:
    if data_kind not in _KIND_TIER:
        log.warning(
            "not recording freshness for unknown kind %r (%s %s): %s",
            data_kind, owner_type, owner_id, error,
        )
        return
    storage.upsert_esi_freshness(
        owner_type, owner_id, data_kind, success=False, error=str(error),
    )
    try:
        clear_stale_owner_kind(
            owner_type, owner_id, data_kind,
            tier_interval_hours=_tier_hours(data_kind),
            stale_clear_multiples=_stale_clear_multiples(),
            owner_name=None,
        )
    except Exception:  # noqa: BLE001 - stale clear must not hide the fetch error
        log.warning(
            "stale clear failed for %s %s %s", owner_type, owner_id, data_kind,
            exc_info=True,
        )


def _kinds_for_owner(
    sharing: list[tuple[str, int, str, str]], owner_type: str, owner_id: int,
) -> list[str]:
    kinds: list[str] = []
    seen: set[str] = set()
    # Registry order, not sharing-row order, so assets land before blueprints
    # (replace_blueprints walks the matching asset table).
    wanted = {kind for ot, oid, kind, _tool in sharing if ot == owner_type and oid == owner_id}
    for spec in OWNED_DATA_KINDS:
        if spec.key in wanted and spec.key not in seen:
            if owner_type == "corporation" and spec.corporation_scope is None:
                continue
            seen.add(spec.key)
            kinds.append(spec.key)
    return kinds


def _run_character_owner(
    *,
    client: ESIClient,
    owner_id: int,
    kinds: list[str],
    characters: list[TokenRecord],
    extra: dict,
    tokens: TokenManager,
) -> dict:
    owner_type = "character"
    if not _try_begin_owner(owner_type, owner_id):
        return {
            "owner_type": owner_type, "owner_id": owner_id,
            "skipped": "in_flight", "kinds": {}, "ok": True,
        }
    owner_name = _display_name(owner_type, owner_id, characters, {})
    kind_report: dict = {}
    failed: Optional[tuple[str, BaseException]] = None
    try:
        with storage.batch_session():
            for data_kind in kinds:
                scope = _required_scope(data_kind, owner_type)
                role = select_auth_role(owner_id, scope, tokens=tokens) if scope else None
                if role is None:
                    # Distinguishable from a fetch failure and from "nothing
                    # shared": Characters will map this to pending re-auth.
                    kind_report[data_kind] = REAUTH_NEEDED
                    continue
                try:
                    wrote = _run_kind(
                        client=client, data_kind=data_kind, owner_type=owner_type,
                        owner_id=owner_id, auth_role=role, owner_name=owner_name,
                        extra=extra,
                    )
                except ESIError as e:
                    kind_report[data_kind] = f"skipped ({e})"
                    failed = (data_kind, e)
                    raise
                _record_success(owner_type, owner_id, data_kind)
                kind_report[data_kind] = wrote
    except Exception as e:  # noqa: BLE001 - owner task must not abort the pass
        if failed is None:
            failed = ("?", e)
            kind_report["error"] = str(e)
    finally:
        _end_owner(owner_type, owner_id)

    if failed is not None:
        # Freshness for the failed kind is recorded *outside* the rolled-back
        # batch so last_error survives (decision 6 / 7).
        _record_failure(owner_type, owner_id, failed[0], failed[1])
        return {
            "owner_type": owner_type, "owner_id": owner_id, "name": owner_name,
            "kinds": kind_report, "ok": False, "error": str(failed[1]),
        }
    return {
        "owner_type": owner_type, "owner_id": owner_id, "name": owner_name,
        "kinds": kind_report, "ok": True,
    }


def _run_corporation_kinds_for_members(
    *,
    client: ESIClient,
    corp_id: int,
    kinds: list[str],
    members: list[TokenRecord],
    corp_name: str,
    extra: dict,
    tokens: TokenManager,
) -> dict:
    """Sequential member retry. Each kind is claimed independently so a
    Director who cannot read orders still contributes assets/jobs/bps, and a
    later Accountant fills orders (and a later Junior_Accountant, wallet).
    Each member's `auth_role` comes from the selector for that kind's corp
    scope — not from a prefix listing.
    """
    owner_type = "corporation"
    if not _try_begin_owner(owner_type, corp_id):
        return {
            "owner_type": owner_type, "owner_id": corp_id,
            "skipped": "in_flight", "kinds": {}, "ok": True,
        }
    owner_name = f"{corp_name} (corp)"
    kind_report: dict = {}
    failed_kinds: dict[str, str] = {}
    try:
        with storage.batch_session():
            for data_kind in kinds:
                scope = _required_scope(data_kind, owner_type)
                last_error: Optional[BaseException] = None
                wrote = None
                any_candidate = False
                for rec in members:
                    role = select_auth_role(rec.character_id, scope, tokens=tokens) if scope else None
                    if role is None:
                        continue
                    any_candidate = True
                    try:
                        wrote = _run_kind(
                            client=client, data_kind=data_kind, owner_type=owner_type,
                            owner_id=corp_id, auth_role=role, owner_name=owner_name,
                            extra=extra,
                        )
                        last_error = None
                        break
                    except ESIError as e:
                        last_error = e
                        continue
                if not any_candidate:
                    kind_report[data_kind] = REAUTH_NEEDED
                    continue
                if last_error is not None or wrote is None:
                    failed_kinds[data_kind] = str(last_error or "no member could fetch")
                    kind_report[data_kind] = f"skipped ({failed_kinds[data_kind]})"
                    # Abort the batch so a later kind cannot commit a
                    # half-written earlier kind from this same owner-run.
                    raise RuntimeError(failed_kinds[data_kind])
                _record_success(owner_type, corp_id, data_kind)
                kind_report[data_kind] = wrote
    except Exception as e:  # noqa: BLE001
        if not failed_kinds:
            failed_kinds["?"] = str(e)
    finally:
        _end_owner(owner_type, corp_id)

    if failed_kinds:
        # Record failure for the first unclaimed kind (the one that aborted).
        first_kind = next(iter(failed_kinds))
        _record_failure(
            owner_type, corp_id, first_kind,
            RuntimeError(failed_kinds[first_kind]),
        )
        return {
            "owner_type": owner_type, "owner_id": corp_id, "name": owner_name,
            "kinds": kind_report, "ok": False, "error": failed_kinds[first_kind],
        }
    return {
        "owner_type": owner_type, "owner_id": corp_id, "name": owner_name,
        "kinds": kind_report, "ok": True,
    }


def _owners_from_sharing(
    sharing: list[tuple[str, int, str, str]],
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    chars: dict[int, None] = {}
    corps: dict[int, None] = {}
    for owner_type, owner_id, _kind, _tool in sharing:
        if owner_type == "character":
            chars.setdefault(owner_id, None)
        elif owner_type == "corporation":
            corps.setdefault(owner_id, None)
    return (
        [("character", cid) for cid in chars],
        [("corporation", oid) for oid in corps],
    )


def _sync(
    sharing: list[tuple[str, int, str, str]],
    *,
    client: Optional[ESIClient],
    tool_key: Optional[str],
    extra: Optional[dict] = None,
) -> dict:
    tm = TokenManager(OAUTH_CONFIG)
    characters = _list_known_characters(tm)
    esi = client or ESIClient(tokens=tm)
    char_owners, corp_owners = _owners_from_sharing(sharing)

    fetch_extra = extra or {}
    char_results: list[dict] = []
    if char_owners:
        def _one_char(pair: tuple[str, int]) -> dict:
            _ot, owner_id = pair
            kinds = _kinds_for_owner(sharing, "character", owner_id)
            return _run_character_owner(
                client=esi, owner_id=owner_id, kinds=kinds,
                characters=characters, extra=fetch_extra, tokens=tm,
            )

        # 4, not 8 (confirmed real incident 2026-09-21): each worker holds
        # its own storage.batch_session() connection open for its whole
        # per-owner fetch (Decision 7's atomic-rollback-per-owner needs
        # that), against a pool of only 10 (storage.py's ConnectionPool
        # max_size). 8 concurrent owners already used most of the pool on
        # their own before anything went wrong; combined with a slow patch
        # (mass token refresh right after a backfill, or a slow ESI
        # response), that left too little headroom for every other request
        # (a login, a page load) needing a connection. 4 keeps a normal tick
        # (usually well under 10 owners) just as parallel in practice while
        # leaving most of the pool free for the rest of the app even in the
        # worst case where every worker is simultaneously slow.
        wrapped = storage.with_current_tenant(_one_char)
        with ThreadPoolExecutor(max_workers=min(4, len(char_owners))) as pool:
            char_results = list(pool.map(wrapped, char_owners))

    # Corp membership from public info, in character-list order. Sequential
    # across corps AND across members of one corp — parallelizing that races
    # two characters to claim the same corp.
    members_by_corp: dict[int, list[TokenRecord]] = {}
    corp_names: dict[int, str] = {}
    for rec in characters:
        try:
            info = esi.character_public_info(rec.character_id)
        except Exception:  # noqa: BLE001 - skip this character's corp
            continue
        corp_id = info.get("corporation_id") if isinstance(info, dict) else None
        if not corp_id:
            continue
        corp_id = int(corp_id)
        members_by_corp.setdefault(corp_id, []).append(rec)
        if corp_id not in corp_names:
            try:
                corp_names[corp_id] = esi.corporation_public_info(corp_id).get("name", str(corp_id))
            except ESIError:
                corp_names[corp_id] = str(corp_id)

    corp_results: list[dict] = []
    for _ot, corp_id in corp_owners:
        kinds = _kinds_for_owner(sharing, "corporation", corp_id)
        members = members_by_corp.get(corp_id) or []
        corp_results.append(_run_corporation_kinds_for_members(
            client=esi, corp_id=corp_id, kinds=kinds, members=members,
            corp_name=corp_names.get(corp_id, str(corp_id)),
            extra=fetch_extra, tokens=tm,
        ))

    all_results = char_results + corp_results
    any_failed = any(not r.get("ok", True) for r in all_results if r.get("skipped") != "in_flight")
    any_in_flight = any(r.get("skipped") == "in_flight" for r in all_results)
    attempted = [r for r in all_results if r.get("skipped") != "in_flight"]
    sweep = None
    # Decision 6: do not sweep if any owner failed *or* is still in flight
    # on a concurrent pass (that pass may yet fail and would otherwise lose
    # this owner's pre-Phase-1 NULL-id rows).
    if attempted and not any_failed and not any_in_flight:
        sweep = storage.sweep_unattributed_null_owner_ids()

    per_character = {
        r.get("name") or str(r["owner_id"]): r.get("kinds") or r.get("skipped")
        for r in char_results
    }
    per_corporation = {
        r.get("name") or str(r["owner_id"]): r.get("kinds") or r.get("skipped")
        for r in corp_results
    }
    return {
        "tool_key": tool_key,
        "characters": per_character,
        "corporations": per_corporation,
        "owners": all_results,
        "ok": not any_failed,
        "null_id_sweep": sweep,
    }


def do_sync_for_tool(
    tool_key: str,
    *,
    client: Optional[ESIClient] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Refresh every (owner, kind) shared with `tool_key`.

    `extra` is forwarded to fetchers (Doctrine passes `structure_id` so
    the contracts fetcher can pre-filter before the per-contract items
    call). Kinds still run once per owner; `_kinds_for_owner` unique's
    them.
    """
    if not tool_key:
        raise RuntimeError("do_sync_for_tool requires a tool_key")
    if tool_key not in ALL_TOOL_KEYS:
        raise RuntimeError(f"unknown tool_key {tool_key!r}")
    sharing = storage.list_esi_sharing(tool_key=tool_key)
    return _sync(sharing, client=client, tool_key=tool_key, extra=extra)


def do_sync_all(*, client: Optional[ESIClient] = None, extra: Optional[dict] = None) -> dict:
    """Refresh every shared (owner, kind) for the current tenant.

    Pass the full sharing list, not a per-(owner, kind) first-tool
    slice: `_kinds_for_owner` already runs each kind once, unioned
    across every consuming tool.
    """
    sharing = storage.list_esi_sharing()
    return _sync(sharing, client=client, tool_key=None, extra=extra)


def do_sync_due(
    *,
    client: Optional[ESIClient] = None,
    extra: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Refresh every shared (owner, kind) whose last_success_at is older
    than that kind's freshness-tier interval (or missing).

    Manual `do_sync_for_tool` / `do_sync_all` still fetch regardless of
    due-ness and stamp freshness, which pushes those pairs past the next
    scheduled tick. The scheduler calls this once per tenant.
    """
    sharing = storage.list_esi_sharing()
    due = [
        row for row in sharing
        if _kind_is_due(row[0], row[1], row[2], now=now)
    ]
    return _sync(due, client=client, tool_key=None, extra=extra)

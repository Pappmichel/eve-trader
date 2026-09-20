"""ESI fetch orchestrator: one task per owner, kinds sequential inside it.

Sharing decides *what* to refresh; prefix listings still resolve tokens
(Phase 4 replaces that). Group 3 is not an orchestrator kind. This module
imports no tool package.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .. import storage
from ..access_gate import ALL_TOOL_KEYS
from ..auth import TOOL_ROLE_PREFIXES, TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from .backfill import prefixes_holding_kind
from .fetchers import fetcher_for
from .registry import OWNED_DATA_KINDS, TIER_FREQUENT, TIER_NORMAL, TIER_RARE
from .stale import DEFAULT_STALE_CLEAR_MULTIPLES, clear_stale_owner_kind

log = logging.getLogger(__name__)

# Hardcoded until Phase 7 adds TradingConfig.esi_*_interval_hours. Matches
# today's production_sync_interval_hours=6 for the normal tier.
DEFAULT_TIER_INTERVAL_HOURS = {
    TIER_FREQUENT: 1.0,
    TIER_NORMAL: 6.0,
    TIER_RARE: 24.0,
}

_KIND_TIER = {k.key: k.freshness_tier for k in OWNED_DATA_KINDS}

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
    return DEFAULT_TIER_INTERVAL_HOURS[_KIND_TIER[data_kind]]


def _list_token_characters(tm: TokenManager) -> list[tuple[str, int, str, str]]:
    """`(role, character_id, character_name, prefix)` from today's prefix
    listings. One character with several prefixes appears once per role.
    """
    out: list[tuple[str, int, str, str]] = []
    for prefixes in TOOL_ROLE_PREFIXES.values():
        for prefix in prefixes:
            for role in tm.list_roles(prefix):
                record = tm.get_record(role)
                if record is not None:
                    out.append((role, record.character_id, record.character_name, prefix))
    return out


def _auth_roles_for(
    character_id: int,
    data_kind: str,
    owner_type: str,
    tokens: list[tuple[str, int, str, str]],
) -> list[str]:
    """Roles for this character that historically carried `data_kind`, in
    prefix-grant order, then any remaining role for the same character."""
    preferred = set(prefixes_holding_kind(data_kind, owner_type))
    roles: list[str] = []
    seen: set[str] = set()
    for role, cid, _name, prefix in tokens:
        if cid != character_id or prefix not in preferred or role in seen:
            continue
        seen.add(role)
        roles.append(role)
    for role, cid, _name, _prefix in tokens:
        if cid == character_id and role not in seen:
            seen.add(role)
            roles.append(role)
    return roles


def _display_name(
    owner_type: str, owner_id: int, tokens: list[tuple[str, int, str, str]],
    corp_names: dict[int, str],
) -> str:
    if owner_type == "character":
        for _role, cid, name, _prefix in tokens:
            if cid == owner_id:
                return name or str(owner_id)
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
    doctrine_assets: bool,
    extra: dict,
) -> dict:
    fn = fetcher_for(data_kind, owner_type, doctrine_assets=doctrine_assets)
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
            stale_clear_multiples=DEFAULT_STALE_CLEAR_MULTIPLES,
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


def _tools_for_owner_kind(
    sharing: list[tuple[str, int, str, str]], owner_type: str, owner_id: int, data_kind: str,
) -> set[str]:
    return {
        tool for ot, oid, kind, tool in sharing
        if ot == owner_type and oid == owner_id and kind == data_kind
    }


def _run_character_owner(
    *,
    client: ESIClient,
    owner_id: int,
    kinds: list[str],
    tokens: list[tuple[str, int, str, str]],
    sharing: list[tuple[str, int, str, str]],
    extra: dict,
) -> dict:
    owner_type = "character"
    if not _try_begin_owner(owner_type, owner_id):
        return {
            "owner_type": owner_type, "owner_id": owner_id,
            "skipped": "in_flight", "kinds": {}, "ok": True,
        }
    owner_name = _display_name(owner_type, owner_id, tokens, {})
    kind_report: dict = {}
    failed: Optional[tuple[str, BaseException]] = None
    try:
        with storage.batch_session():
            for data_kind in kinds:
                roles = _auth_roles_for(owner_id, data_kind, owner_type, tokens)
                if not roles:
                    err = RuntimeError(f"no token for character {owner_id} kind {data_kind}")
                    kind_report[data_kind] = f"skipped ({err})"
                    failed = (data_kind, err)
                    raise err
                doctrine_assets = (
                    data_kind == "assets"
                    and "doctrine" in _tools_for_owner_kind(sharing, owner_type, owner_id, data_kind)
                    and "production" not in _tools_for_owner_kind(sharing, owner_type, owner_id, data_kind)
                    and "sorting" not in _tools_for_owner_kind(sharing, owner_type, owner_id, data_kind)
                    and "trading" not in _tools_for_owner_kind(sharing, owner_type, owner_id, data_kind)
                )
                last_error: Optional[BaseException] = None
                wrote = None
                used_role: Optional[str] = None
                for role in roles:
                    try:
                        wrote = _run_kind(
                            client=client, data_kind=data_kind, owner_type=owner_type,
                            owner_id=owner_id, auth_role=role, owner_name=owner_name,
                            doctrine_assets=doctrine_assets, extra=extra,
                        )
                        last_error = None
                        used_role = role
                        break
                    except ESIError as e:
                        last_error = e
                        continue
                if last_error is not None or wrote is None:
                    err = last_error or RuntimeError("fetch returned nothing")
                    kind_report[data_kind] = f"skipped ({err})"
                    failed = (data_kind, err)
                    raise err
                _record_success(owner_type, owner_id, data_kind)
                kind_report[data_kind] = wrote
                # A character shared with both production and doctrine still
                # needs the doctrine asset tables filled until Phase 3b.
                if (
                    data_kind == "assets"
                    and not doctrine_assets
                    and used_role is not None
                    and "doctrine" in _tools_for_owner_kind(sharing, owner_type, owner_id, data_kind)
                ):
                    _run_kind(
                        client=client, data_kind=data_kind, owner_type=owner_type,
                        owner_id=owner_id, auth_role=used_role, owner_name=owner_name,
                        doctrine_assets=True, extra=extra,
                    )
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
    members: list[tuple[str, int, str]],
    sharing: list[tuple[str, int, str, str]],
    corp_name: str,
    extra: dict,
) -> dict:
    """Sequential member retry. Each kind is claimed independently so a
    Director who cannot read orders still contributes assets/jobs/bps, and a
    later Accountant fills orders (and a later Junior_Accountant, wallet).
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
                doctrine_assets = (
                    data_kind == "assets"
                    and "doctrine" in _tools_for_owner_kind(sharing, owner_type, corp_id, data_kind)
                    and "production" not in _tools_for_owner_kind(sharing, owner_type, corp_id, data_kind)
                    and "sorting" not in _tools_for_owner_kind(sharing, owner_type, corp_id, data_kind)
                    and "trading" not in _tools_for_owner_kind(sharing, owner_type, corp_id, data_kind)
                )
                last_error: Optional[BaseException] = None
                wrote = None
                used_role: Optional[str] = None
                for role, _cid, _cname in members:
                    try:
                        wrote = _run_kind(
                            client=client, data_kind=data_kind, owner_type=owner_type,
                            owner_id=corp_id, auth_role=role, owner_name=owner_name,
                            doctrine_assets=doctrine_assets, extra=extra,
                        )
                        last_error = None
                        used_role = role
                        break
                    except ESIError as e:
                        last_error = e
                        continue
                if last_error is not None or wrote is None:
                    failed_kinds[data_kind] = str(last_error or "no member could fetch")
                    kind_report[data_kind] = f"skipped ({failed_kinds[data_kind]})"
                    # Abort the batch so a later kind cannot commit a
                    # half-written earlier kind from this same owner-run.
                    raise RuntimeError(failed_kinds[data_kind])
                _record_success(owner_type, corp_id, data_kind)
                kind_report[data_kind] = wrote
                if (
                    data_kind == "assets"
                    and not doctrine_assets
                    and used_role is not None
                    and "doctrine" in _tools_for_owner_kind(sharing, owner_type, corp_id, data_kind)
                ):
                    _run_kind(
                        client=client, data_kind="assets", owner_type=owner_type,
                        owner_id=corp_id, auth_role=used_role, owner_name=owner_name,
                        doctrine_assets=True, extra=extra,
                    )
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
    tokens = _list_token_characters(tm)
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
                tokens=tokens, sharing=sharing, extra=fetch_extra,
            )

        wrapped = storage.with_current_tenant(_one_char)
        with ThreadPoolExecutor(max_workers=min(8, len(char_owners))) as pool:
            # list() preserves sharing/token order, which is what the sequential
            # corp-claim pass below uses as member order.
            char_results = list(pool.map(wrapped, char_owners))

    # Corp membership from public info, in character-list order. Sequential
    # across corps AND across members of one corp — parallelizing that races
    # two characters to claim the same corp.
    members_by_corp: dict[int, list[tuple[str, int, str]]] = {}
    corp_names: dict[int, str] = {}
    seen_member: set[tuple[int, str]] = set()
    for role, cid, cname, _prefix in tokens:
        try:
            info = esi.character_public_info(cid)
        except Exception:  # noqa: BLE001 - skip this character's corp
            continue
        corp_id = info.get("corporation_id") if isinstance(info, dict) else None
        if not corp_id:
            continue
        corp_id = int(corp_id)
        key = (cid, role)
        if key in seen_member:
            continue
        seen_member.add(key)
        members_by_corp.setdefault(corp_id, []).append((role, cid, cname))
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
            sharing=sharing, corp_name=corp_names.get(corp_id, str(corp_id)),
            extra=fetch_extra,
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
    them. The full sharing list is kept so dual-write until 3b can see
    every consuming tool.
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
    slice: `_kinds_for_owner` already runs each kind once, and
    `_tools_for_owner_kind` needs every consuming tool (doctrine
    dual-write until 3b).
    """
    sharing = storage.list_esi_sharing()
    return _sync(sharing, client=client, tool_key=None, extra=extra)

"""Character Info `do_*` actions (docs/CHARACTER_MANAGEMENT_PLAN.md phase 1).

UI-agnostic, no FastAPI imports. The router in `api/routers/char_info.py` is
a thin wrapper around these.

Every field a caller can see is gated the same way, in this order:
1. not shared with `char_info`  -> state `not_shared` (nothing is fetched)
2. no token holds the scope     -> state `reauth_needed`
3. otherwise the data (snapshot read, or a live ESI call for the live-only
   kinds location/ship/online)   -> state `ok`, `not_synced` or `error`

A failing field never fails the whole page: a dead token for one character
must not blank the overview of the others.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from ..esi_data import (
    AccessorError,
    do_sync_for_tool,
    is_shared,
    read_esi,
    select_auth_role,
)
from ..esi_data import actions as esi_actions
from ..esi_data.registry import OWNED_DATA_KINDS

log = logging.getLogger(__name__)

TOOL_KEY = "char_info"

STATE_OK = "ok"
STATE_NOT_SHARED = "not_shared"
STATE_REAUTH = "reauth_needed"
STATE_NOT_SYNCED = "not_synced"
STATE_ERROR = "error"

_SCOPE_BY_KIND = {k.key: k.character_scope for k in OWNED_DATA_KINDS}

# Overview fans out one worker per character. Kept small: each worker's ESI
# calls are cached/short and the pool of DB connections is shared with the
# rest of the app (CLAUDE.md "Connection pool sizing").
_MAX_WORKERS = 4


def _field(state: str, value: Any = None, detail: Optional[str] = None) -> dict:
    out: dict = {"state": state, "value": value}
    if detail:
        out["detail"] = detail
    return out


def _gate(kind: str, character_id: int, tokens: TokenManager) -> Optional[str]:
    """None if the caller may proceed, else the state to report."""
    if not is_shared(kind, TOOL_KEY, "character", character_id):
        return STATE_NOT_SHARED
    if select_auth_role(character_id, _SCOPE_BY_KIND[kind], tokens=tokens) is None:
        return STATE_REAUTH
    return None


def _live(
    kind: str, character_id: int, tokens: TokenManager, fetch: Callable[[str], Any],
    shape: Callable[[Any], Any] = lambda v: v,
) -> dict:
    blocked = _gate(kind, character_id, tokens)
    if blocked:
        return _field(blocked)
    role = select_auth_role(character_id, _SCOPE_BY_KIND[kind], tokens=tokens)
    try:
        return _field(STATE_OK, shape(fetch(role)))
    except ESIError as e:
        return _field(STATE_ERROR, detail=str(e)[:200])


def _freshness_by_kind(character_id: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ot, oid, kind, success, attempt, err in storage.list_esi_freshness():
        if ot == "character" and oid == character_id:
            out[kind] = {"last_success_at": success, "last_attempt_at": attempt, "last_error": err}
    return out


def _snapshot(
    kind: str, character_id: int, tokens: TokenManager, freshness: dict[str, dict],
    shape: Callable[[list[dict]], Any],
) -> dict:
    blocked = _gate(kind, character_id, tokens)
    if blocked:
        return _field(blocked)
    fresh = freshness.get(kind)
    last_success = fresh["last_success_at"] if fresh else None
    detail = fresh["last_error"] if fresh and fresh.get("last_error") else None
    if last_success is None:
        return _field(STATE_NOT_SYNCED, detail=detail)
    rows = read_esi(kind, TOOL_KEY, owner_type="character", owner_id=character_id)
    out = _field(STATE_OK, shape(rows), detail)
    out["synced_at"] = last_success
    return out


# --------------------------------------------------------------- name lookup
def _names(client: ESIClient, ids: list[int]) -> dict[int, str]:
    ids = [i for i in dict.fromkeys(int(i) for i in ids if i)]
    if not ids:
        return {}
    try:
        return client.resolve_names(ids)
    except ESIError:
        return {}


def _location_value(raw: dict) -> dict:
    """Live ESI location -> ids plus best-effort names from the local SDE /
    structure-name caches (no live structure resolution: that needs a
    different scope and belongs to Production's resolution chain)."""
    system_id = raw.get("solar_system_id")
    location_id = raw.get("structure_id") or raw.get("station_id")
    system_name = None
    if system_id:
        system_name = storage.get_solar_system_names([system_id]).get(int(system_id))
    location_name = None
    if location_id:
        location_name = storage.get_location_names([int(location_id)]).get(int(location_id))
    return {
        "solar_system_id": system_id,
        "solar_system_name": system_name,
        "location_id": location_id,
        "location_kind": "structure" if raw.get("structure_id") else ("station" if raw.get("station_id") else "space"),
        "location_name": location_name,
    }


def _ship_value(raw: dict) -> dict:
    type_id = raw.get("ship_type_id")
    row = storage.get_sde_type(int(type_id)) if type_id else None
    return {
        "ship_type_id": type_id,
        "ship_type_name": row[2] if row else None,
        "ship_name": raw.get("ship_name"),
    }


# ------------------------------------------------------------------- public
def _public_info(client: ESIClient, character_id: int) -> dict:
    try:
        info = client.character_public_info(character_id)
    except ESIError:
        return {}
    return info if isinstance(info, dict) else {}


def _character_summary(
    token_char: dict, client: ESIClient, tokens: TokenManager, *, detail: bool,
) -> dict:
    """Everything the overview row needs; `detail` adds the heavier reads."""
    cid = int(token_char["character_id"])
    info = _public_info(client, cid)
    alliance_id = info.get("alliance_id")
    alliance_name = _names(client, [alliance_id]).get(int(alliance_id)) if alliance_id else None
    freshness = _freshness_by_kind(cid)

    out: dict = {
        "character_id": cid,
        "character_name": token_char.get("character_name") or info.get("name"),
        "corporation_id": token_char.get("corporation_id") or info.get("corporation_id"),
        "corporation_name": token_char.get("corporation_name"),
        "alliance_id": alliance_id,
        "alliance_name": alliance_name,
        "security_status": info.get("security_status"),
        "birthday": info.get("birthday"),
        "wallet_balance": _snapshot(
            "wallet_balance", cid, tokens, freshness,
            lambda rows: rows[0]["balance"] if rows else None,
        ),
        "location": _live(
            "location", cid, tokens, lambda role: client.character_location(cid, role), _location_value,
        ),
        "ship": _live(
            "ship", cid, tokens, lambda role: client.character_ship(cid, role), _ship_value,
        ),
        "online": _live("online", cid, tokens, lambda role: client.character_online(cid, role)),
        "freshness": freshness,
    }
    if detail:
        out["standings"] = _standings_field(cid, client, tokens, freshness)
        out["loyalty_points"] = _loyalty_field(cid, client, tokens, freshness)
        out["corporation_history"] = _corp_history(cid, client)
    return out


def _standings_field(cid: int, client: ESIClient, tokens: TokenManager, freshness: dict) -> dict:
    def shape(rows: list[dict]) -> dict:
        names = _names(client, [r["from_id"] for r in rows])
        grouped: dict[str, list[dict]] = {"faction": [], "npc_corp": [], "agent": []}
        for r in rows:
            grouped.setdefault(r["from_type"], []).append({
                "from_id": r["from_id"],
                "name": names.get(r["from_id"]) or f"#{r['from_id']}",
                "standing": r["standing"],
            })
        for lst in grouped.values():
            lst.sort(key=lambda x: -x["standing"])
        return grouped
    return _snapshot("standings", cid, tokens, freshness, shape)


def _loyalty_field(cid: int, client: ESIClient, tokens: TokenManager, freshness: dict) -> dict:
    def shape(rows: list[dict]) -> list[dict]:
        names = _names(client, [r["corporation_id"] for r in rows])
        return [
            {
                "corporation_id": r["corporation_id"],
                "corporation_name": names.get(r["corporation_id"]) or f"#{r['corporation_id']}",
                "loyalty_points": r["loyalty_points"],
            }
            for r in rows
        ]
    return _snapshot("loyalty", cid, tokens, freshness, shape)


def _corp_history(cid: int, client: ESIClient) -> list[dict]:
    """Public data, no sharing gate. Newest first."""
    try:
        history = client.character_corporation_history(cid)
    except ESIError:
        return []
    names = _names(client, [h["corporation_id"] for h in history])
    rows = [
        {
            "corporation_id": h["corporation_id"],
            "corporation_name": names.get(h["corporation_id"]) or f"#{h['corporation_id']}",
            "start_date": h.get("start_date"),
        }
        for h in history
    ]
    rows.sort(key=lambda r: r["start_date"] or "", reverse=True)
    return rows


# ------------------------------------------------------------------- actions
def _client_and_tokens() -> tuple[ESIClient, TokenManager]:
    tokens = TokenManager(OAUTH_CONFIG)
    return ESIClient(tokens=tokens), tokens


def do_list_character_overview() -> dict:
    """One row per registered ESI character (any character with a token)."""
    token_chars = esi_actions.do_list_token_characters()
    if not token_chars:
        return {"characters": []}
    client, tokens = _client_and_tokens()

    # ThreadPoolExecutor workers do not inherit contextvars (CLAUDE.md).
    work = storage.with_current_tenant(
        lambda tc: _character_summary(tc, client, tokens, detail=False)
    )
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(token_chars))) as pool:
        rows = list(pool.map(work, token_chars))
    return {"characters": rows}


def do_character_detail(character_id: int) -> dict:
    try:
        character_id = int(character_id)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid character_id {character_id!r}") from e
    match = next(
        (c for c in esi_actions.do_list_token_characters() if c["character_id"] == character_id),
        None,
    )
    if match is None:
        raise ActionError(f"Character {character_id} is not registered for ESI access.")
    client, tokens = _client_and_tokens()
    return _character_summary(match, client, tokens, detail=True)


def do_sync_char_info() -> dict:
    """Refresh the snapshot kinds shared with Character Info (wallet
    balance, standings, loyalty points). Live kinds need no sync.

    `in_flight` lists owners another pass is already syncing - the UI must
    say "sync already running" for those, not treat them as success or
    failure (docs/CHARACTER_MANAGEMENT_PLAN.md R10).
    """
    try:
        result = do_sync_for_tool(TOOL_KEY)
    except AccessorError as e:  # pragma: no cover - defensive, tool key is fixed
        raise ActionError(str(e)) from e
    owners = result.get("owners", [])
    return {
        "ok": bool(result.get("ok")),
        "characters": result.get("characters", {}),
        "in_flight": [o["owner_id"] for o in owners if o.get("skipped") == "in_flight"],
        "failed": [
            {"owner_id": o["owner_id"], "name": o.get("name"), "error": o.get("error")}
            for o in owners if not o.get("ok", True) and o.get("skipped") != "in_flight"
        ],
    }

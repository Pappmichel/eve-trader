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
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from ..esi_data import AccessorError, do_sync_for_tool, select_auth_role
from ..esi_data import actions as esi_actions
from . import fields
from .fields import SCOPE_BY_KIND, STATE_ERROR, STATE_OK, field as _field

log = logging.getLogger(__name__)

TOOL_KEY = "char_info"

# Overview fans out one worker per character. Kept small: each worker's ESI
# calls are cached/short and the pool of DB connections is shared with the
# rest of the app (CLAUDE.md "Connection pool sizing").
_MAX_WORKERS = 4


def _gate(kind: str, character_id: int, tokens: TokenManager) -> Optional[str]:
    return fields.gate(kind, TOOL_KEY, character_id, tokens)


def _live(
    kind: str, character_id: int, tokens: TokenManager, fetch: Callable[[str], Any],
    shape: Callable[[Any], Any] = lambda v: v,
) -> dict:
    blocked = _gate(kind, character_id, tokens)
    if blocked:
        return _field(blocked)
    role = select_auth_role(character_id, SCOPE_BY_KIND[kind], tokens=tokens)
    try:
        return _field(STATE_OK, shape(fetch(role)))
    except ESIError as e:
        return _field(STATE_ERROR, detail=str(e)[:200])


def _freshness_by_kind(character_id: int) -> dict[str, dict]:
    return fields.freshness_by_kind(character_id)


def _snapshot(
    kind: str, character_id: int, tokens: TokenManager, freshness: dict[str, dict],
    shape: Callable[[list[dict]], Any],
) -> dict:
    return fields.snapshot(kind, TOOL_KEY, character_id, tokens, freshness, shape)


# --------------------------------------------------------------- name lookup
def _names(client: ESIClient, ids: list[int]) -> dict[int, str]:
    ids = [i for i in dict.fromkeys(int(i) for i in ids if i)]
    if not ids:
        return {}
    try:
        return client.resolve_names(ids)
    except ESIError:
        return {}


def _fatigue_value(raw: Any) -> dict:
    """The three optional ESI dates as ISO strings (None when absent). The
    countdown itself is the browser's job: it needs the current time, and a
    server-side remaining-seconds would already be stale when displayed."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "jump_fatigue_expire_date": raw.get("jump_fatigue_expire_date"),
        "last_jump_date": raw.get("last_jump_date"),
        "last_update_date": raw.get("last_update_date"),
    }


# Clones can be jumped again this long after the last jump. 24 h is the base
# cooldown; the Infomorph Synchronizing skill shortens it by up to 4 h, which
# is not read here, so the UI calls this the latest the cooldown can end.
CLONE_JUMP_COOLDOWN_HOURS = 24


def _clone_jump_available_at(last_jump: Any) -> Optional[str]:
    parsed = fields.parse_dt(last_jump)
    return (parsed + timedelta(hours=CLONE_JUMP_COOLDOWN_HOURS)).isoformat() if parsed else None


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
        "fatigue": _live("fatigue", cid, tokens, lambda role: client.character_fatigue(cid, role), _fatigue_value),
        "freshness": freshness,
    }
    if detail:
        out["standings"] = _standings_field(cid, client, tokens, freshness)
        out["loyalty_points"] = _loyalty_field(cid, client, tokens, freshness)
        out["clones"] = _clones_field(cid, tokens, freshness)
        out["implants"] = _implants_field(cid, tokens, freshness)
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


def _type_names(type_ids) -> dict[int, str]:
    ids = sorted({int(t) for t in type_ids})
    if not ids:
        return {}
    return {tid: row[2] for tid, row in storage.get_sde_types_bulk(ids).items() if row}


def _implant_list(type_ids, names: dict[int, str]) -> list[dict]:
    return sorted(
        ({"type_id": int(t), "name": names.get(int(t)) or f"Type {t}"} for t in type_ids),
        key=lambda i: i["name"],
    )


def _clones_field(cid: int, tokens: TokenManager, freshness: dict) -> dict:
    """Home location plus jump clones with their implants. Location names come
    from the same lookup chain as everything else (SDE stations, global and
    per-tenant structure-name caches); an unresolved one keeps its id."""
    def shape(rows: list[dict]) -> dict:
        row = rows[0] if rows else None
        if row is None:
            return {"home": None, "jump_clones": [], "last_clone_jump_date": None,
                    "last_station_change_date": None, "clone_jump_available_at": None}
        meta, clones = row["meta"], row["jump_clones"]
        loc_ids = [c["location_id"] for c in clones if c["location_id"]]
        if meta["home_location_id"]:
            loc_ids.append(meta["home_location_id"])
        loc_names = storage.get_location_names([int(i) for i in loc_ids]) if loc_ids else {}
        names = _type_names(t for c in clones for t in c["implants"])
        home = None
        if meta["home_location_id"]:
            home = {
                "location_id": meta["home_location_id"], "location_type": meta["home_location_type"],
                "location_name": loc_names.get(int(meta["home_location_id"])),
            }
        return {
            "home": home,
            "jump_clones": [
                {
                    "jump_clone_id": c["jump_clone_id"], "name": c["name"],
                    "location_id": c["location_id"], "location_type": c["location_type"],
                    "location_name": loc_names.get(int(c["location_id"])) if c["location_id"] else None,
                    "implants": _implant_list(c["implants"], names),
                }
                for c in clones
            ],
            "last_clone_jump_date": meta["last_clone_jump_date"],
            "last_station_change_date": meta["last_station_change_date"],
            "clone_jump_available_at": _clone_jump_available_at(meta["last_clone_jump_date"]),
        }
    return _snapshot("clones", cid, tokens, freshness, shape)


def _implants_field(cid: int, tokens: TokenManager, freshness: dict) -> dict:
    def shape(rows: list[dict]) -> list[dict]:
        return _implant_list([r["type_id"] for r in rows], _type_names(r["type_id"] for r in rows))
    return _snapshot("implants", cid, tokens, freshness, shape)


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


JOURNAL_MAX_ROWS = 500
JOURNAL_WINDOW_DAYS = 30      # ESI's own window; nothing older exists to show


def _journal_value(raw: list) -> dict:
    """Newest first, capped, plus totals over *every* entry (not just the rows
    shown): income, expenses and a per-`ref_type` breakdown."""
    entries = sorted(
        (e for e in raw if isinstance(e, dict) and e.get("date")),
        key=lambda e: (str(e["date"]), int(e.get("id") or 0)), reverse=True,
    )
    income = sum(float(e.get("amount") or 0) for e in entries if float(e.get("amount") or 0) > 0)
    expense = sum(float(e.get("amount") or 0) for e in entries if float(e.get("amount") or 0) < 0)
    by_type: dict[str, float] = {}
    for e in entries:
        by_type[e.get("ref_type") or "unknown"] = by_type.get(e.get("ref_type") or "unknown", 0.0) + float(e.get("amount") or 0)
    return {
        "window_days": JOURNAL_WINDOW_DAYS,
        "entries": [
            {
                "id": e.get("id"), "date": e["date"], "ref_type": e.get("ref_type"),
                "amount": float(e.get("amount") or 0),
                "balance": float(e["balance"]) if e.get("balance") is not None else None,
                "description": e.get("description"),
            }
            for e in entries[:JOURNAL_MAX_ROWS]
        ],
        "total_entries": len(entries),
        "truncated": len(entries) > JOURNAL_MAX_ROWS,
        "income": income, "expense": expense,
        "by_type": sorted(
            ({"ref_type": t, "total": v} for t, v in by_type.items()), key=lambda x: -abs(x["total"]),
        ),
    }


def do_wallet_journal(character_id: int) -> dict:
    """The character's wallet journal for ESI's 30-day window, read live (2 min
    cache) and never stored - it is not the journal Trading syncs. Gated by the
    Wallet checkbox of Character Info (the `wallet_balance` sharing row)."""
    try:
        character_id = int(character_id)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid character_id {character_id!r}") from e
    if not any(c["character_id"] == character_id for c in fields.token_characters()):
        raise ActionError(f"Character {character_id} is not registered for ESI access.")
    client, tokens = _client_and_tokens()
    return fields.live(
        "wallet_balance", TOOL_KEY, character_id, tokens,
        lambda role: client.character_wallet_journal_live(character_id, role), _journal_value,
    )


def do_sync_char_info() -> dict:
    """Refresh the snapshot kinds shared with Character Info (wallet
    balance, standings, loyalty points, clones, implants). Live kinds need no sync.

    `in_flight` lists owners another pass is already syncing - the UI must
    say "sync already running" for those, not treat them as success or
    failure (docs/CHARACTER_MANAGEMENT_PLAN.md R10).
    """
    try:
        result = do_sync_for_tool(TOOL_KEY)
    except AccessorError as e:  # pragma: no cover - defensive, tool key is fixed
        raise ActionError(str(e)) from e
    return fields.summarise_sync(result)

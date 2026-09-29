"""Shared field-state helpers for the Character Management sub-tools.

Every value a sub-tool page can show is either data or a reason it is
missing. The gate order is the same everywhere (docs/CHARACTER_MANAGEMENT_
PLAN.md phase 1):
1. not shared with the sub-tool's `tool_key`  -> `not_shared` (nothing read)
2. no token holds the kind's scope            -> `reauth_needed`
3. otherwise the data                          -> `ok`, `not_synced` or `error`

A failing field never fails a whole page: one dead token must not blank the
view of the other characters.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_data import is_shared, read_esi, select_auth_role
from ..esi_data.registry import OWNED_DATA_KINDS

STATE_OK = "ok"
STATE_NOT_SHARED = "not_shared"
STATE_REAUTH = "reauth_needed"
STATE_NOT_SYNCED = "not_synced"
STATE_ERROR = "error"

SCOPE_BY_KIND = {k.key: k.character_scope for k in OWNED_DATA_KINDS}


def field(state: str, value: Any = None, detail: Optional[str] = None) -> dict:
    out: dict = {"state": state, "value": value}
    if detail:
        out["detail"] = detail
    return out


def gate(kind: str, tool_key: str, character_id: int, tokens: TokenManager) -> Optional[str]:
    """None if the caller may proceed, else the state to report."""
    if not is_shared(kind, tool_key, "character", character_id):
        return STATE_NOT_SHARED
    if select_auth_role(character_id, SCOPE_BY_KIND[kind], tokens=tokens) is None:
        return STATE_REAUTH
    return None


def freshness_by_kind(character_id: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ot, oid, kind, success, attempt, err in storage.list_esi_freshness():
        if ot == "character" and oid == character_id:
            out[kind] = {"last_success_at": success, "last_attempt_at": attempt, "last_error": err}
    return out


def snapshot(
    kind: str, tool_key: str, character_id: int, tokens: TokenManager,
    freshness: dict[str, dict], shape: Callable[[list[dict]], Any], **read_filters: Any,
) -> dict:
    """A snapshot-synced kind read through the fail-closed accessor.
    `not_synced` (never a successful sync) is told apart from `ok` with no
    rows, which is a legitimate empty result."""
    blocked = gate(kind, tool_key, character_id, tokens)
    if blocked:
        return field(blocked)
    fresh = freshness.get(kind)
    last_success = fresh["last_success_at"] if fresh else None
    detail = fresh["last_error"] if fresh and fresh.get("last_error") else None
    if last_success is None:
        return field(STATE_NOT_SYNCED, detail=detail)
    rows = read_esi(kind, tool_key, owner_type="character", owner_id=character_id, **read_filters)
    out = field(STATE_OK, shape(rows), detail)
    out["synced_at"] = last_success
    return out


def summarise_sync(result: dict) -> dict:
    """Shape of every sub-tool's sync response. `in_flight` lists owners
    another pass was already syncing - the UI must say "sync already
    running" for those, not treat them as success or failure (plan R10)."""
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


def token_characters() -> list[dict]:
    """Every character with a token, sorted by name. No network: unlike
    esi_data.actions.do_list_token_characters this does not look up corps."""
    by_id: dict[int, str] = {}
    for rec in TokenManager(OAUTH_CONFIG).list_records():
        by_id[rec.character_id] = by_id.get(rec.character_id) or rec.character_name or ""
    return sorted(
        ({"character_id": cid, "character_name": name or f"#{cid}"} for cid, name in by_id.items()),
        key=lambda c: (c["character_name"].lower(), c["character_id"]),
    )


def parse_dt(value: Any) -> Optional[datetime]:
    """ESI / Postgres ISO timestamp -> aware datetime (None if missing/bad)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


_HTTP_STATUS = re.compile(r"HTTP (\d{3})")


def esi_failure(error: BaseException) -> str:
    """A message safe to show and log for a failed ESI request on private data.

    `ESIError` text embeds the start of the response body and the URL. For mail
    that is more than we want in an error toast or a log line, so only the
    HTTP status (or "network error") survives (docs/CHARACTER_MANAGEMENT_PLAN.md
    phase 3, privacy)."""
    if "Token refresh failed" in str(error):
        return "Token refresh failed - re-authorize this character on the Characters page"
    match = _HTTP_STATUS.search(str(error))
    return f"ESI returned HTTP {match.group(1)}" if match else "ESI request failed (network error)"


def capability_ready(
    character_id: int, capability_key: str, scope: str, tokens: TokenManager,
) -> str:
    """State of a write capability for one character: `ready` (ticked AND a
    token holds the scope), `not_enabled` (not ticked - the user has not
    consented to this app acting for the character), or `reauth_needed`
    (ticked, but no stored token carries the scope yet)."""
    ticked = any(
        cid == int(character_id) and key == capability_key
        for cid, key in storage.list_esi_character_capabilities()
    )
    if not ticked:
        return "not_enabled"
    if select_auth_role(character_id, scope, tokens=tokens) is None:
        return STATE_REAUTH
    return "ready"

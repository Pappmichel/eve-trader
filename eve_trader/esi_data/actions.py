"""Characters-tool `do_*` functions (docs/ESI_ACCESS_PLAN.md Phase 6).

UI-agnostic, no FastAPI imports. The Characters router and page call
these. Prefix-login `/start` is gone as of Phase 9.

This module imports no tool package. Requested SSO scopes are passed in
by the caller (auth.py already knows each prefix's bundle).
"""
from __future__ import annotations

from typing import Iterable, Optional

from .. import storage
from ..access_gate import ALL_TOOL_KEYS
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from . import orchestrator
from .registry import ACCESS_CAPABILITIES, OWNED_DATA_KINDS, consuming_tool_keys
from .selector import character_has_token_pool, normalize_scopes, reauth_write_role


class ActionError(RuntimeError):
    """User-facing Characters error — same role as `eve_trader.actions.ActionError`."""


_KIND_BY_KEY = {k.key: k for k in OWNED_DATA_KINDS}
_CAP_BY_KEY = {c.key: c for c in ACCESS_CAPABILITIES}


def _union_token_scopes(character_id: int) -> frozenset[str]:
    tm = TokenManager(OAUTH_CONFIG)
    scopes: set[str] = set()
    for rec in tm.list_records():
        if rec.character_id == int(character_id):
            scopes.update(normalize_scopes(rec.scopes))
    return frozenset(scopes)


def _scopes_for_kind_or_cap(key: str) -> set[str]:
    kind = _KIND_BY_KEY.get(key)
    if kind is not None:
        out = {kind.character_scope}
        if kind.corporation_scope:
            out.add(kind.corporation_scope)
        return out
    cap = _CAP_BY_KEY.get(key)
    if cap is not None:
        out = {cap.character_scope}
        if cap.corporation_scope:
            out.add(cap.corporation_scope)
        return out
    raise ActionError(f"Unknown data kind or capability {key!r}")


def do_access_preview(
    requested_scopes: Iterable[str],
    *,
    character_id: Optional[int] = None,
    title: str = "Confirm data access",
) -> dict:
    """Confirm-dialog payload: registry items in `requested_scopes`.

    `added` is true when none of that item's requested scopes are on any
    existing token for `character_id`. With no character (first prefix
    login) every item is added. Identity-only `gate` does not call this.
    """
    requested = normalize_scopes(requested_scopes)
    existing: frozenset[str] = frozenset()
    if character_id is not None:
        existing = _union_token_scopes(character_id)

    items: list[dict] = []
    for kind in OWNED_DATA_KINDS:
        kind_scopes = {kind.character_scope}
        if kind.corporation_scope:
            kind_scopes.add(kind.corporation_scope)
        asking = kind_scopes & requested
        if not asking:
            continue
        items.append({
            "key": kind.key,
            "label": kind.label,
            "group": kind.group,
            "added": not bool(asking & existing),
        })
    for cap in ACCESS_CAPABILITIES:
        cap_scopes = {cap.character_scope}
        if cap.corporation_scope:
            cap_scopes.add(cap.corporation_scope)
        asking = cap_scopes & requested
        if not asking:
            continue
        items.append({
            "key": cap.key,
            "label": cap.label,
            "group": 3,
            "added": not bool(asking & existing),
        })
    return {"title": title, "items": items}


def do_reauth_scopes(
    character_id: int, extra_kinds: Optional[Iterable[str]] = None,
) -> list[str]:
    """SSO scope list for a Characters re-auth: existing union extra kinds."""
    extra = list(extra_kinds or [])
    unknown = [k for k in extra if k not in _KIND_BY_KEY and k not in _CAP_BY_KEY]
    if unknown:
        raise ActionError(f"Unknown data kind(s): {', '.join(sorted(unknown))}")
    scopes = set(_union_token_scopes(character_id))
    for key in extra:
        scopes.update(_scopes_for_kind_or_cap(key))
    if not scopes:
        raise ActionError(
            "Nothing to request — this character has no token and no extra kinds."
        )
    return sorted(scopes)


def do_list_sharing(tool_key: Optional[str] = None) -> list[dict]:
    rows = storage.list_esi_sharing(tool_key)
    return [
        {"owner_type": ot, "owner_id": oid, "data_kind": kind, "tool_key": tk}
        for ot, oid, kind, tk in rows
    ]


def do_list_freshness() -> list[dict]:
    return [
        {
            "owner_type": ot, "owner_id": oid, "data_kind": kind,
            "last_success_at": success, "last_attempt_at": attempt, "last_error": err,
        }
        for ot, oid, kind, success, attempt, err in storage.list_esi_freshness()
    ]


def do_list_capabilities() -> list[dict]:
    return [
        {"character_id": cid, "capability_key": key}
        for cid, key in storage.list_esi_character_capabilities()
    ]


def do_list_token_characters() -> list[dict]:
    tm = TokenManager(OAUTH_CONFIG)
    by_id: dict[int, dict] = {}
    for rec in tm.list_records():
        slot = by_id.setdefault(rec.character_id, {
            "character_id": rec.character_id,
            "character_name": rec.character_name,
            "roles": [],
        })
        slot["roles"].append(rec.role)
        if rec.character_name:
            slot["character_name"] = rec.character_name
    out = []
    for cid, slot in sorted(by_id.items()):
        out.append({
            "character_id": cid,
            "character_name": slot["character_name"],
            "write_role": reauth_write_role(cid),
            "character_has_token_pool": character_has_token_pool(cid),
            "roles": sorted(slot["roles"]),
        })
    return out


def do_set_sharing(
    owner_type: str,
    owner_id: int,
    data_kind: str,
    tool_key: str,
    enabled: bool,
) -> dict:
    if owner_type not in ("character", "corporation"):
        raise ActionError(f"Unknown owner_type {owner_type!r}")
    kind = _KIND_BY_KEY.get(data_kind)
    if kind is None:
        raise ActionError(f"Unknown data_kind {data_kind!r}")
    if tool_key not in ALL_TOOL_KEYS:
        raise ActionError(f"Unknown tool_key {tool_key!r}")
    if tool_key not in kind.consuming_tools:
        raise ActionError(
            f"{tool_key!r} does not consume {data_kind!r} — consuming tools are "
            f"{', '.join(kind.consuming_tools)}"
        )
    if enabled:
        storage.upsert_esi_sharing(owner_type, owner_id, data_kind, tool_key)
    else:
        storage.delete_esi_sharing(owner_type, owner_id, data_kind, tool_key)
    if tool_key == "production":
        # production/engine.py caches shared_production_owner_ids (Known
        # gap 3, docs/ESI_ACCESS_PLAN.md) rather than re-querying esi_sharing
        # on every type_id in a demand loop - this is the one write path
        # that can make that cache stale. Lazy import: this module must not
        # depend on the production package at module level (esi_data is a
        # cross-cutting package - see CLAUDE.md's "Two tools, one backend").
        from ..production.engine import invalidate_shared_production_owner_ids_cache
        invalidate_shared_production_owner_ids_cache()
    return {
        "owner_type": owner_type, "owner_id": int(owner_id),
        "data_kind": data_kind, "tool_key": tool_key, "enabled": enabled,
    }


def do_set_capability(character_id: int, capability_key: str, enabled: bool) -> dict:
    if capability_key not in _CAP_BY_KEY:
        raise ActionError(f"Unknown capability {capability_key!r}")
    if enabled:
        storage.upsert_esi_character_capability(character_id, capability_key)
    else:
        storage.delete_esi_character_capability(character_id, capability_key)
    return {
        "character_id": int(character_id),
        "capability_key": capability_key,
        "enabled": enabled,
    }


def do_sync(tool_key: Optional[str] = None) -> dict:
    if tool_key is not None:
        if tool_key not in ALL_TOOL_KEYS:
            raise ActionError(f"Unknown tool_key {tool_key!r}")
        if tool_key not in consuming_tool_keys():
            raise ActionError(f"{tool_key!r} does not consume ESI data")
        return orchestrator.do_sync_for_tool(tool_key)
    return orchestrator.do_sync_all()

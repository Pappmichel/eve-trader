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
from .selector import character_has_token_pool, normalize_scopes, reauth_write_role, select_auth_role


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


def _cached_corporation_id(client, character_id: int) -> Optional[int]:
    """Best-effort corporation_id for `character_id` via the cached,
    public, unauthenticated character_public_info - never raises (docs/
    ESI_ACCESS_PLAN.md Known gap 2). None on any failure/missing field;
    callers treat that as "cannot resolve", not an error."""
    try:
        info = client.character_public_info(character_id)
    except Exception:  # noqa: BLE001 - best-effort; a page load must not 500 on one character's lookup failing
        return None
    corp_id = info.get("corporation_id") if isinstance(info, dict) else None
    return int(corp_id) if corp_id else None


def _cached_corporation_name(client, corp_id: Optional[int]) -> Optional[str]:
    """Best-effort corporation name for `corp_id` via the cached, public,
    unauthenticated corporation_public_info - mirrors _cached_corporation_id
    above (never raises, None on any failure/missing corp_id/missing field;
    the Characters page falls back to displaying the raw ID when this is
    None)."""
    if not corp_id:
        return None
    try:
        info = client.corporation_public_info(corp_id)
    except Exception:  # noqa: BLE001 - best-effort; a page load must not 500 on one corp's lookup failing
        return None
    name = info.get("name") if isinstance(info, dict) else None
    return str(name) if name else None


def do_list_token_characters() -> list[dict]:
    from ..esi_client import ESIClient

    tm = TokenManager(OAUTH_CONFIG)
    client = ESIClient(tokens=tm)
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
        corp_id = _cached_corporation_id(client, cid)
        out.append({
            "character_id": cid,
            "character_name": slot["character_name"],
            "write_role": reauth_write_role(cid),
            "character_has_token_pool": character_has_token_pool(cid),
            "roles": sorted(slot["roles"]),
            # Known gap 2's "access via" column - the corporation this
            # character belongs to, cached (ESIClient.character_public_info),
            # public data, best-effort (None if the lookup fails).
            "corporation_id": corp_id,
            # Resolved display name for corporation_id above (also cached,
            # public, best-effort) - the Characters page used to show the
            # raw corporation_id with no name at all (confirmed real gap
            # 2026-09-21).
            "corporation_name": _cached_corporation_name(client, corp_id),
        })
    return out


def do_remove_token_character(character_id: int) -> dict:
    """Drop every ESI token for `character_id`.

    Character-centric, not prefix-centric: a re-auth-merged `buyer:<id>`
    (or `doctrine:<id>`, `esi:<id>`, ...) is this character's only key and
    may be feeding every tool they share with, so a per-tool "Remove" that
    deletes by prefix would also drop Production/Doctrine/Sorting. Removal
    belongs here, where the sharing matrix is visible.

    `esi_sharing` (this character's owner rows) and
    `esi_character_capabilities` are deliberately KEPT, matching
    `admin.do_remove_user` not deleting the orphaned tenant's data.
    Re-adding the same character via Add character restores the matrix
    without re-ticking. Snapshot tables and `esi_freshness` are also left
    intact (decision 4: sharing filters reads, it does not erase last
    week's snapshots). Corporation sharing rows are not this character's
    and are never touched.

    Listings that resolve a token (`do_list_token_characters`,
    `list_shared_*_characters`) omit the character immediately.
    """
    try:
        character_id = int(character_id)
    except (TypeError, ValueError) as e:
        raise ActionError(f"Invalid character_id {character_id!r}") from e
    if character_id <= 0:
        raise ActionError(f"Invalid character_id {character_id}.")

    tm = TokenManager(OAUTH_CONFIG)
    records = [r for r in tm.list_records() if r.character_id == character_id]
    if not records:
        raise ActionError(f"No ESI token stored for character {character_id}.")

    roles = sorted({r.role for r in records})
    name = next((r.character_name for r in records if r.character_name), str(character_id))
    shared_tools = sorted({
        tk for ot, oid, _kind, tk in storage.list_esi_sharing()
        if ot == "character" and int(oid) == character_id
    })
    capabilities = sorted({
        key for cid, key in storage.list_esi_character_capabilities()
        if int(cid) == character_id
    })

    for role in roles:
        tm.remove_token(role)

    return {
        "removed": character_id,
        "character_name": name,
        "roles": roles,
        "shared_tools": shared_tools,
        "capabilities": capabilities,
    }


def do_check_corporation_roles() -> dict:
    """Known gap 2's role warning: for every corp a registered character
    belongs to, and every Group-1 data kind with corp_roles set (Assets/
    Industry Jobs/Blueprints -> Director; Market Orders -> Accountant or
    Trader; Wallet -> Accountant or Junior_Accountant), whether at least
    one member whose roles were actually checked holds a needed role.

    A character's roles are only checked when they have the
    "corporation_roles" capability ticked (Access section, Characters
    page) AND a token carrying esi-characters.read_corporation_roles.v1 -
    live ESI, not cached (a stale role warning defeats its own purpose).
    A corp with zero checked members reports has_role=None ("cannot
    verify") for every kind, never a false "missing" - matches the
    fallback this replaces ("a live 403 stays the real check": silence
    when unverifiable, not an alarm).

    Returns {"corporations": [{"corporation_id", "checked_characters",
    "unchecked_characters", "data_kinds": {kind_key: {"required_roles",
    "has_role"}}}]} - only for corp_roles-bearing kinds, only for corps
    with at least one registered member."""
    from ..esi_client import ESIClient, ESIError

    tm = TokenManager(OAUTH_CONFIG)
    client = ESIClient(tokens=tm)
    cap = _CAP_BY_KEY["corporation_roles"]

    members_by_corp: dict[int, list[tuple[int, str]]] = {}
    for rec in tm.list_records():
        corp_id = _cached_corporation_id(client, rec.character_id)
        if corp_id is None:
            continue
        pair = (rec.character_id, rec.character_name)
        bucket = members_by_corp.setdefault(corp_id, [])
        if pair not in bucket:
            bucket.append(pair)

    role_kinds = [k for k in OWNED_DATA_KINDS if k.corp_roles]
    capable_ids = {
        cid for cid, key in storage.list_esi_character_capabilities() if key == "corporation_roles"
    }

    corporations = []
    for corp_id, members in sorted(members_by_corp.items()):
        checked_roles: set[str] = set()
        checked_names: list[str] = []
        unchecked_names: list[str] = []
        for character_id, character_name in members:
            role = (select_auth_role(character_id, cap.character_scope, tokens=tm)
                    if character_id in capable_ids else None)
            if role is None:
                unchecked_names.append(character_name)
                continue
            try:
                roles_resp = client.character_roles(character_id, auth_role=role)
            except ESIError:
                unchecked_names.append(character_name)
                continue
            checked_roles.update(roles_resp.get("roles") or [])
            checked_names.append(character_name)

        data_kinds = {}
        for kind in role_kinds:
            if not checked_names:
                data_kinds[kind.key] = {"required_roles": list(kind.corp_roles), "has_role": None}
            else:
                data_kinds[kind.key] = {
                    "required_roles": list(kind.corp_roles),
                    "has_role": bool(checked_roles & set(kind.corp_roles)),
                }
        corporations.append({
            "corporation_id": corp_id,
            "checked_characters": sorted(checked_names),
            "unchecked_characters": sorted(unchecked_names),
            "data_kinds": data_kinds,
        })
    return {"corporations": corporations}


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

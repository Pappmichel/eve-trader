"""Admin tool: user/tool-grant management. A deliberate cross-tenant
superadmin surface, not a per-tenant self-service page - only characters
registered to storage.DEFAULT_TENANT_ID ever see the "admin" tool at all
(see access_gate.tools_for), so every function in this module reads/writes
across every tenant, same as storage.py's own tenant_registry_entries/
tool_grants (both deliberately unscoped, not RLS'd - see docs/admin_
schema.sql). Cross-cutting, like portfolio.py, but with do_* actions since
this module (unlike portfolio.py) actually writes.

Same actions-pattern every other tool follows (see CLAUDE.md's "Architecture:
actions.py is the one entry point") - api/routers/admin.py calls these same
do_* functions, no logic duplicated in the router.
"""
from __future__ import annotations

import requests

from . import access_gate, storage
from .actions import ActionError
from .esi_client import ESIError
from .production import jita_price_cache, sde
from .production.engine import invalidate_discover_cache, invalidate_ship_margin_cache


def do_list_tenants() -> list[dict]:
    return [
        {"tenant_id": str(tenant_id), "name": name, "created_at": str(created_at) if created_at else None}
        for tenant_id, name, created_at in storage.list_tenants()
    ]


def do_list_users() -> list[dict]:
    return storage.list_users_with_grants()


def do_add_user(character_name: str) -> dict:
    """Registers a character as a brand-new user, looked up by name (exact,
    case-insensitive match via ESI's /universe/ids/ search - character_id
    isn't something an admin would have on hand, unlike the character's own
    name) - always creates a fresh, dedicated tenant for them (named after
    the admin-provided name; self-heals to ESI's own canonical casing the
    first time this character actually logs in, see auth.py's callback()
    gate branch) rather than assigning them into an existing tenant, so two
    characters can never end up sharing one tenant's data (also backed by a
    DB-level UNIQUE constraint on tenant_registry_entries.tenant_id, see
    docs/admin_schema.sql - this is belt-and-suspenders, not the only
    guarantee)."""
    character_name = character_name.strip()
    if not character_name:
        raise ActionError("Character name can't be empty.")

    from .esi_client import ESIClient  # local import: avoids a hard dependency for callers that don't need it
    try:
        character_id = ESIClient().character_search(character_name)
    except ESIError as e:
        raise ActionError(f"Could not resolve character '{character_name}' via ESI: {e}") from e
    if character_id is None:
        raise ActionError(f"No character found named '{character_name}'.")

    tenant_id = storage.create_tenant(character_name)
    storage.add_tenant_registry_entry(tenant_id, character_id, character_name=character_name)
    return {"character_id": character_id, "character_name": character_name, "tenant_id": tenant_id}


def do_remove_user(character_id: int) -> dict:
    """Deregisters `character_id` entirely - both their login (tenant_
    registry_entries) and every tool grant they had. Idempotent (matches
    storage.remove_tenant_registry_entry/revoke_all_tool_grants's own
    no-op-if-missing semantics) - removing an already-removed/never-added
    character isn't an error."""
    storage.remove_tenant_registry_entry(character_id)
    storage.revoke_all_tool_grants(character_id)
    return {"removed": character_id}


def do_refresh_sde() -> dict:
    """Downloads and caches the current Fuzzwork SDE export - moved here from
    production/actions.py (GitHub issue #34): the SDE cache (sde_types/
    sde_blueprint_materials/etc.) is global, shared data, not per-tenant, so
    triggering a refresh affects every tenant's Production/Trading data at
    once - a cross-tenant-impacting action that belongs in this module's
    superadmin surface, not exposed to every Production tenant individually.
    production/actions.py's do_check_sde_freshness (read-only) stays there,
    unaffected - it still legitimately informs Production's/Trading's own
    per-tenant sidebars."""
    try:
        result = sde.refresh_sde()
    except requests.RequestException as e:
        # Confirmed real gap (see the original do_refresh_sde in production/
        # actions.py this was moved from): sde.refresh_sde()'s network errors
        # used to reach the router unconverted - a raw 500 instead of the
        # ActionError every other ESI/Goonmetrics-touching action converts a
        # network failure to.
        raise ActionError(f"SDE refresh failed: {e}") from e
    # all_tenants=True: the SDE cache is global (this function's own
    # docstring), so a refresh can change every tenant's discover/margin
    # results, not just the calling admin's own - confirmed real gap (GitHub
    # issue #54's own fix): the per-tenant-only default left every other
    # tenant serving stale results for up to the full cache TTL.
    invalidate_discover_cache(all_tenants=True)
    invalidate_ship_margin_cache(all_tenants=True)
    return result


def do_refresh_jita_price_cache() -> dict:
    """Standalone manual trigger for the shared Jita price cache (production/
    jita_price_cache.py) - deliberately its own action, not called from
    anywhere else (do_refresh_production and friends only ever *read* the
    cache, via pricing.jita_prices), so a user wanting genuinely fresh Jita
    prices right now can force one without it silently piggybacking on, or
    blocking, any other action. Also normally refreshed automatically once
    an hour by the scheduler (see scheduler._check_and_run_jita_price_cache_
    job) - same cross-tenant-impacting-cache reasoning as do_refresh_sde
    above, since the cache is shared/global, not per-tenant."""
    count = jita_price_cache.refresh_jita_price_cache()
    return {"cached_type_ids": count, "updated_at": jita_price_cache.last_updated_at()}


def do_set_tool_grants(character_id: int, tool_keys: list[str]) -> dict:
    """Replaces the complete set of tools granted to `character_id` with
    exactly `tool_keys` (replace, not append/merge - matches doctrine/
    storage.py's replace_fitting_items's own "replace, not append"
    semantics) - unchecked checkboxes in the Admin UI must actually revoke,
    not just leave old grants stranded."""
    unknown = set(tool_keys) - set(access_gate.ALL_TOOL_KEYS)
    if unknown:
        raise ActionError(f"Unknown tool_key(s): {', '.join(sorted(unknown))}")

    users = {u["character_id"]: u for u in do_list_users()}
    user = users.get(character_id)
    if user is None:
        raise ActionError(f"Character {character_id} isn't a registered user - add them first.")

    storage.revoke_all_tool_grants(character_id)
    for tool_key in tool_keys:
        storage.set_tool_grant(character_id, tool_key, user["tenant_id"])
    return {"character_id": character_id, "tool_keys": sorted(tool_keys)}

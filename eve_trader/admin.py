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

from . import access_gate, storage
from .actions import ActionError
from .esi_client import ESIError


def do_list_tenants() -> list[dict]:
    return [
        {"tenant_id": str(tenant_id), "name": name, "created_at": str(created_at) if created_at else None}
        for tenant_id, name, created_at in storage.list_tenants()
    ]


def do_list_users() -> list[dict]:
    return storage.list_users_with_grants()


def do_add_user(character_id: int) -> dict:
    """Registers `character_id` as a brand-new user - always creates a
    fresh, dedicated tenant for them (named after their own resolved
    character name) rather than assigning them into an existing tenant, so
    two characters can never end up sharing one tenant's data (also backed
    by a DB-level UNIQUE constraint on tenant_registry_entries.tenant_id,
    see docs/admin_schema.sql - this is belt-and-suspenders, not the only
    guarantee). Resolves the character's current name via ESI's public
    character endpoint (no auth needed) so the Admin UI's user list has a
    name to show immediately, without waiting for that character's own
    first gate login (see auth.py's callback() gate branch, which refreshes
    this same cached name on every future login)."""
    from .esi_client import ESIClient  # local import: avoids a hard dependency for callers that don't need it
    try:
        character_name = ESIClient().character_public_info(character_id).get("name")
    except ESIError as e:
        raise ActionError(f"Could not resolve character {character_id} via ESI: {e}") from e
    if not character_name:
        raise ActionError(f"No character found for id {character_id}.")

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

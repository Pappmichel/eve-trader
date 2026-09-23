"""Admin tool: user/tool-grant management. A deliberate cross-tenant
superadmin surface, not a per-tenant self-service page. Reachable only to
characters with an explicit "admin" tool grant (issued by another admin or
by `do_bootstrap_admin` / `eve-trader admin bootstrap`). Every function in
this module reads/writes across every tenant, same as storage.py's own
tenant_registry_entries/tool_grants (both deliberately unscoped, not RLS'd
- see docs/admin_schema.sql). Cross-cutting, like portfolio.py, but with
do_* actions since this module (unlike portfolio.py) actually writes.

Same actions-pattern every other tool follows (see CLAUDE.md's "Architecture:
actions.py is the one entry point") - api/routers/admin.py calls these same
do_* functions, no logic duplicated in the router.
"""
from __future__ import annotations

import logging

import requests

from . import access_gate, backup, storage
from .actions import ActionError
from .esi_client import ESIError
from .production import jita_price_cache, sde, sde_diff
from .production.engine import invalidate_discover_cache, invalidate_ship_margin_cache
from .production.sde import FetchedSde

log = logging.getLogger("eve_trader.admin")

# Single-process deploy (uvicorn without --workers, see
# deploy/eve-trader.service.template) - the preview's parsed dump stays here
# until Apply, rather than being re-fetched or staged in Postgres.
_staged_sde: FetchedSde | None = None


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


def do_start_sde_preview() -> dict:
    """Kicks off SDE preview as a background job. The HTTP handler must not
    block on the ~14 sequential CSV downloads."""
    from . import pipeline_runner
    return pipeline_runner.start_sde_preview()


def do_sde_preview_status() -> dict:
    from . import pipeline_runner
    return pipeline_runner.job_status(pipeline_runner.TOOL_ADMIN)


def do_preview_sde(progress_callback=None) -> dict:
    return _preview_worker(progress_callback)


def _preview_worker(progress_callback=None) -> dict:
    """Fetch+parse Fuzzwork CSVs, diff against the current cache, stage the
    parsed dump in `_staged_sde`. The job result is the diff only - raw CSV
    rows must not land in pipeline_runs.result."""
    global _staged_sde
    try:
        fetched = sde.fetch_sde(progress_callback=progress_callback)
    except requests.RequestException as e:
        # Confirmed real gap (see the original do_refresh_sde in production/
        # actions.py this was moved from): the fetch's network errors used
        # to reach the router unconverted - a raw 500 instead of the
        # ActionError every other ESI/Goonmetrics-touching action converts a
        # network failure to.
        raise ActionError(f"SDE refresh failed: {e}") from e
    snapshot = storage.get_sde_snapshot_for_diff()
    diff = sde_diff.build_diff(fetched, snapshot)
    _staged_sde = fetched
    return diff


def do_apply_sde() -> dict:
    """Writes the staged preview dump into the shared SDE cache. Moved here
    from production/actions.py (GitHub issue #34): the SDE cache (sde_types/
    sde_blueprint_materials/etc.) is global, shared data, not per-tenant, so
    applying a refresh affects every tenant's Production/Trading data at
    once - a cross-tenant-impacting action that belongs in this module's
    superadmin surface, not exposed to every Production tenant individually.
    production/actions.py's do_check_sde_freshness (read-only) stays there,
    unaffected - it still legitimately informs Production's/Trading's own
    per-tenant sidebars."""
    global _staged_sde
    if _staged_sde is None:
        raise ActionError("Keine Preview-Daten vorhanden - bitte SDE-Update erneut prüfen.")
    result = sde.apply_sde(_staged_sde)
    # all_tenants=True: the SDE cache is global (this function's own
    # docstring), so a refresh can change every tenant's discover/margin
    # results, not just the calling admin's own - confirmed real gap (GitHub
    # issue #54's own fix): the per-tenant-only default left every other
    # tenant serving stale results for up to the full cache TTL.
    invalidate_discover_cache(all_tenants=True)
    invalidate_ship_margin_cache(all_tenants=True)
    _staged_sde = None
    return result


def do_refresh_jita_price_cache() -> dict:
    """Standalone manual trigger for the shared Jita price cache (production/
    jita_price_cache.py) - deliberately its own action, not called from
    anywhere else (do_refresh_production and friends only ever *read* the
    cache, via pricing.jita_prices), so a user wanting genuinely fresh Jita
    prices right now can force one without it silently piggybacking on, or
    blocking, any other action. Also normally refreshed automatically once
    an hour by the scheduler (see scheduler._check_and_run_jita_price_cache_
    job) - same cross-tenant-impacting-cache reasoning as do_apply_sde
    above, since the cache is shared/global, not per-tenant."""
    try:
        count = jita_price_cache.refresh_jita_price_cache()
    except (ESIError, requests.RequestException) as e:
        # refresh_jita_price_cache talks to ESI directly (not via a do_* that
        # already translates). A transport failure here used to 500 the
        # Admin "Refresh Jita prices" button; the scheduler path already
        # records the exception on the job status.
        raise ActionError(f"Could not refresh Jita price cache ({e}).") from e
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


def _require_allowlist_type(entry_type: str) -> None:
    if entry_type not in ("corporation", "alliance"):
        raise ActionError("entry_type must be 'corporation' or 'alliance'.")


def do_list_allowlist() -> list[dict]:
    return storage.list_allowlist()


def do_search_allowlist_candidates(name: str) -> list[dict]:
    """Exact corp/alliance name match. The Admin UI adds by id from this
    list; the name cached on the allowlist is resolved again at add time."""
    name = name.strip()
    if not name:
        raise ActionError("Name can't be empty.")
    from .esi_client import ESIClient
    try:
        return ESIClient().search_corporations_alliances(name)
    except ESIError as e:
        raise ActionError(f"Could not search ESI for '{name}': {e}") from e


def do_add_allowlist_entry(entry_type: str, entry_id: int,
                           added_by_character_id: int | None = None) -> dict:
    """Resolves and caches the display name. The client does not get to
    supply the name."""
    _require_allowlist_type(entry_type)
    if not isinstance(entry_id, int) or entry_id <= 0:
        raise ActionError("entry_id must be a positive integer.")
    from .esi_client import ESIClient
    try:
        names = ESIClient().resolve_names([entry_id])
    except ESIError as e:
        raise ActionError(f"Could not resolve {entry_type} {entry_id} via ESI: {e}") from e
    name = names.get(entry_id)
    if not name:
        raise ActionError(f"No name found for {entry_type} {entry_id}.")
    storage.add_allowlist_entry(entry_type, entry_id, name, added_by_character_id)
    return {"entry_type": entry_type, "entry_id": entry_id, "name": name}


def do_remove_allowlist_entry(entry_type: str, entry_id: int) -> dict:
    _require_allowlist_type(entry_type)
    storage.remove_allowlist_entry(entry_type, entry_id)
    return {"entry_type": entry_type, "entry_id": entry_id}


def _matches_entry(user: dict, entry_type: str, entry_id: int) -> bool:
    if entry_type == "corporation":
        return user.get("corporation_id") == entry_id
    return user.get("alliance_id") == entry_id


def _allowed_by(user: dict, entries: list[dict]) -> bool:
    return any(_matches_entry(user, entry["entry_type"], entry["entry_id"]) for entry in entries)


def do_allowlist_impact(entry_type: str, entry_id: int, action: str,
                        actor_character_id: int | None = None) -> dict:
    """Which registered non-admin users would be suspended by this change.

    Adding the first entry turns the re-check on, so anyone who would not
    match that entry is listed. Adding to a non-empty allowlist only
    expands it. Removing the last entry turns the re-check off, so nobody
    is suspended. Otherwise, users whose only match is the removed entry
    are listed. `actor_exempt_but_affected` is true when the acting admin's
    own affiliation would fall outside the allowlist — admins stay exempt,
    the warning is still shown.
    """
    _require_allowlist_type(entry_type)
    if action not in ("add", "remove"):
        raise ActionError("action must be 'add' or 'remove'.")
    current = storage.list_allowlist()
    users = storage.list_users_with_grants()
    non_admins = [user for user in users if "admin" not in user["tool_keys"]]

    if action == "add":
        activates = len(current) == 0
        after = current + [{"entry_type": entry_type, "entry_id": entry_id}]
        would = [
            user for user in non_admins
            if activates and not _allowed_by(user, after)
        ] if activates else []
        disables = False
    else:
        remaining = [
            entry for entry in current
            if not (entry["entry_type"] == entry_type and entry["entry_id"] == entry_id)
        ]
        disables = len(current) > 0 and len(remaining) == 0
        activates = False
        if disables:
            would = []
        else:
            would = [
                user for user in non_admins
                if _allowed_by(user, current) and not _allowed_by(user, remaining)
            ]
        after = remaining

    actor = next((user for user in users if user["character_id"] == actor_character_id), None)
    actor_affected = False
    if actor is not None and "admin" in actor["tool_keys"]:
        if action == "add" and len(current) == 0:
            actor_affected = not _allowed_by(actor, after)
        elif action == "remove" and not disables:
            actor_affected = _allowed_by(actor, current) and not _allowed_by(actor, after)

    return {
        "would_suspend": [
            {
                "character_id": user["character_id"],
                "character_name": user["character_name"],
                "corporation_id": user["corporation_id"],
                "alliance_id": user["alliance_id"],
            }
            for user in would
        ],
        "activates_recheck": activates,
        "disables_recheck": disables,
        "actor_exempt_but_affected": actor_affected,
    }


def do_refresh_user_affiliations() -> dict:
    """One bulk affiliation call for every registered character. Updates the
    registry (and the suspension flag) so the user table and the impact
    preview are looking at a current corp/alliance."""
    users = storage.list_users_with_grants()
    ids = [user["character_id"] for user in users]
    if not ids:
        return {"updated": 0}
    from .esi_client import ESIClient
    try:
        affiliations = ESIClient().character_affiliation(ids)
    except ESIError as e:
        raise ActionError(f"Could not refresh affiliations via ESI: {e}") from e
    empty = storage.allowlist_is_empty()
    updated = 0
    for user in users:
        found = affiliations.get(user["character_id"])
        if found is None:
            continue
        corporation_id, alliance_id = found
        suspended = (
            not empty
            and "admin" not in user["tool_keys"]
            and not storage.allowlist_contains(corporation_id, alliance_id)
        )
        storage.update_registry_affiliation(
            user["character_id"], corporation_id, alliance_id, suspended,
        )
        updated += 1
    return {"updated": updated}


def do_list_access_requests(status: str | None = None) -> list[dict]:
    if status is not None and status not in ("pending", "approved", "rejected"):
        raise ActionError("status must be pending, approved, or rejected.")
    return storage.list_access_requests(status)


def do_count_pending_access_requests() -> dict:
    return {"pending": storage.count_pending_access_requests()}


def do_approve_access_request(character_id: int, tool_keys: list[str],
                               decided_by_character_id: int | None = None) -> dict:
    """Creates a dedicated tenant named after the character and grants
    exactly `tool_keys`. Invalid keys are rejected before anything is written."""
    unknown = set(tool_keys) - set(access_gate.ALL_TOOL_KEYS)
    if unknown:
        raise ActionError(f"Unknown tool_key(s): {', '.join(sorted(unknown))}")
    try:
        tenant_id = storage.approve_access_request(
            character_id, tool_keys, decided_by_character_id,
        )
    except storage.RegistryConflict as e:
        raise ActionError(str(e)) from e
    return {
        "character_id": character_id,
        "tenant_id": tenant_id,
        "tool_keys": sorted(set(tool_keys)),
    }


def do_reject_access_request(character_id: int, decided_by_character_id: int | None = None) -> dict:
    if not storage.reject_access_request(character_id, decided_by_character_id):
        raise ActionError(f"No pending access request for character {character_id}.")
    return {"character_id": character_id, "status": "rejected"}


def do_delete_access_request(character_id: int) -> dict:
    """Deletes a request in any status. Deleting a rejection is what lets
    that character request access again."""
    storage.delete_access_request(character_id)
    return {"character_id": character_id}


def do_bootstrap_admin(
    character_id: int,
    character_name: str | None = None,
    *,
    confirm: bool = False,
    all_tools: bool = False,
) -> dict:
    """CLI-only operator bootstrap (F-07 / F-NEW-02). Grants `admin` (and
    optionally every tool) to `character_id` without an HTTP backdoor.

    - Requires confirm=True (`--confirm` on the CLI) so a stray invocation
      cannot silently create an admin.
    - Never reassigns an already-registered character to a different tenant.
    - If the character is new and DEFAULT_TENANT_ID has no occupant, they
      are registered there; otherwise a dedicated tenant is created. That
      UNIQUE (tenant_id) constraint is left in place (one character per
      tenant) — it is not an admin-count limit.
    - Idempotent: a second run on the same character re-grants the same
      tools and does not create another tenant.
    """
    if not confirm:
        raise ActionError(
            "Refusing to bootstrap an admin without --confirm. "
            "This grants the admin tool to the named character."
        )
    if not isinstance(character_id, int) or character_id <= 0:
        raise ActionError("character_id must be a positive integer.")
    name = (character_name or "").strip() or None

    existing_tenant = storage.resolve_tenant_id(character_id)
    created_tenant = False
    if existing_tenant is None:
        occupants = storage.list_tenant_registry_entries(storage.DEFAULT_TENANT_ID)
        if not occupants:
            tenant_id = storage.DEFAULT_TENANT_ID
        else:
            tenant_id = storage.create_tenant(name or f"Admin {character_id}")
            created_tenant = True
        storage.add_tenant_registry_entry(tenant_id, character_id, character_name=name)
    else:
        tenant_id = existing_tenant
        if name is not None:
            storage.add_tenant_registry_entry(tenant_id, character_id, character_name=name)

    previous = storage.list_tool_grants_for_character(character_id)
    tools = list(access_gate.ALL_TOOL_KEYS) if all_tools else ["admin"]
    for tool_key in tools:
        storage.set_tool_grant(character_id, tool_key, tenant_id)
    granted = storage.list_tool_grants_for_character(character_id)
    return {
        "character_id": character_id,
        "character_name": name,
        "tenant_id": tenant_id,
        "tool_keys": granted,
        "created_tenant": created_tenant,
        "already_registered": existing_tenant is not None,
        "already_had_admin": "admin" in previous,
    }


def do_create_backup() -> dict:
    """Backs up the whole Postgres database (every tenant, via pg_dump) plus
    config.yaml into a single timestamped .zip (see backup.py) - this app's
    only persistence (no git repo) so this is the only way to recover from a
    lost/corrupted disk short of redoing every ESI sync and Settings change
    by hand.

    Moved here from actions.py/the Portfolio page (confirmed real
    misplacement 2026-09-21): one pg_dump already covers every tenant's
    data in one shot (backup.py's own docstring), so creating a backup is a
    cross-tenant-impacting action - same reasoning as do_start_sde_preview/
    do_refresh_jita_price_cache above, not a per-tenant Portfolio button.
    The access gate already required the "admin" grant for this via a
    one-off exception in api/app.py's _required_tool_for_path (F-06) before
    this move; that exception is gone now that the route lives under
    /api/admin/ like everything else here."""
    try:
        return backup.create_backup()
    except (OSError, RuntimeError) as e:
        # RuntimeError covers BackupError (non-zero pg_dump) and unexpected
        # failures. The exception chain keeps operator diagnostics in logs;
        # the ActionError message is generic so an HTTP 400 cannot leak
        # pg_dump stderr / paths / DSN details (F-NEW-04).
        log.exception("backup failed")
        raise ActionError("Backup failed.") from e


def do_list_backups() -> dict:
    """Read-only - kept alongside do_create_backup above rather than left on
    Portfolio, since a backups list with no admin nearby to act on it isn't
    useful to a non-admin tenant."""
    return {"rows": backup.list_backups()}

"""User actions on one location (docs/LOCATION_RESOLUTION_PLAN.md phase 6),
shared by every tool that shows locations: give an id a tenant-own name, remove
it, or look the id up again now. UI-agnostic, like every other do_* module."""
from __future__ import annotations

from .. import storage
from ..actions import ActionError

MAX_NAME_LENGTH = 100


def do_set_manual_name(location_id: int, name: str) -> dict:
    """Gives `location_id` a tenant-own display name - the tier below every
    ESI-resolved name, for a place no character can resolve (or a name the user
    prefers to keep private). Never written to the shared global cache."""
    name = (name or "").strip()
    if not name:
        raise ActionError("Name must not be empty.")
    if len(name) > MAX_NAME_LENGTH:
        raise ActionError(f"Name must be at most {MAX_NAME_LENGTH} characters.")
    if int(location_id) <= 0:
        raise ActionError("Unknown location.")
    storage.set_manual_location_name(int(location_id), name)
    return {"location_id": int(location_id), "name": name}


def do_remove_manual_name(location_id: int) -> dict:
    storage.remove_manual_location_name(int(location_id))
    return {"location_id": int(location_id)}


def do_retry_lookup(location_id: int) -> dict:
    """Looks one id up again now, regardless of its retry date. An Office or
    container is never sent to ESI; it shows the structure it stands in."""
    from . import resolver

    if int(location_id) <= 0:
        raise ActionError("Unknown location.")
    result = resolver.resolve_ids([int(location_id)], force=True)
    if result.get("skipped") == "already running":
        raise ActionError("A name lookup is already running for this account; try again in a moment.")
    info = (result.get("ids") or {}).get(int(location_id)) or {}
    if result.get("skipped", "").startswith("no character") and info.get("name") is None:
        raise ActionError(
            "No character has ticked Structure name resolution yet (Characters page, Access section)."
        )
    return {"location_id": int(location_id), "name": info.get("name"), "status": info.get("status")}

"""Which characters can help resolve a location (docs/LOCATION_RESOLUTION_PLAN.md
phase 4). Moved here from production/esi_sync so the ESI sync orchestrator can
resolve names without importing a tool package; production/esi_sync keeps thin
wrappers with the same names."""
from __future__ import annotations

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError


def list_capability_characters(capability_key: str, tm: TokenManager | None = None) -> list[tuple[str, int, str]]:
    """Returns (auth_role, character_id, character_name) for every character
    with `capability_key` ticked (Access section, Characters page) and a token
    that actually carries its scope. Group 3 has no tool dimension (decision 9
    of docs/ESI_ACCESS_PLAN.md), so this is gated on
    `esi_character_capabilities`, not `esi_sharing`. A character with the
    capability ticked but no token carrying its scope (needs Re-authorize) is
    omitted, not raised. Raises `ValueError` for an unknown `capability_key`."""
    from ..esi_data.registry import ACCESS_CAPABILITIES
    from ..esi_data.selector import select_auth_role

    cap = next((c for c in ACCESS_CAPABILITIES if c.key == capability_key), None)
    if cap is None:
        raise ValueError(f"unknown capability {capability_key!r}")
    tm = tm or TokenManager(OAUTH_CONFIG)
    char_ids = sorted({
        cid for cid, key in storage.list_esi_character_capabilities() if key == capability_key
    })
    out = []
    for character_id in char_ids:
        role = select_auth_role(character_id, cap.character_scope, tokens=tm)
        if role is None:
            continue
        record = tm.get_record(role)
        out.append((role, character_id, record.character_name if record else str(character_id)))
    return out


def corp_roles_for_characters(client: ESIClient, characters: list[tuple[str, int, str]]) -> dict[int, str]:
    """{corporation_id: role} for the first character (in list order) that
    belongs to each distinct corp - the corp-structure-list lookup only needs
    to try each corp once. Never raises: a character whose corp lookup fails is
    skipped."""
    corp_roles: dict[int, str] = {}
    for role, character_id, _name in characters:
        try:
            corporation_id = client.character_public_info(character_id)["corporation_id"]
        except ESIError:
            continue
        corp_roles.setdefault(corporation_id, role)
    return corp_roles

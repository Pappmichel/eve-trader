"""Syncs character + corp assets/industry-jobs/blueprints from ESI into local
cache tables (see storage.py), so engine.py can compute current stock instead
of relying on manual entry alone.

Fetch is `eve_trader.esi_data.orchestrator.do_sync_for_tool("production")`.
This module keeps character/capability listing (`list_shared_producer_
characters`/`list_capability_characters` - `list_producer_characters` is
superseded, see its own docstring). Structure-name resolution moved to
`eve_trader.locations` (docs/LOCATION_RESOLUTION_PLAN.md). Sequential corp
claim (one corp, members retried in order, Director vs Accountant/Trader
independent) lives in the orchestrator so two characters cannot race to
claim the same corp.
"""
from __future__ import annotations

from typing import Callable, Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from .constants import ACTIVITY_REACTION

PRODUCTION_ROLE_PREFIX = "producer"

# Upwell structure IDs are always >= this (14 digits, e.g. 1049588174021);
# NPC station IDs top out around 8 digits (e.g. 60003760) - a cheap range
# check on a location_id already in hand from an asset row, used by
# resolve_structure_ids' callers to decide what's even worth attempting to
# resolve as a structure, with no SDE lookup needed.
STRUCTURE_ID_MIN = 1_000_000_000_000

PRODUCTION_SCOPES = [
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
    "esi-industry.read_character_jobs.v1",
    "esi-industry.read_corporation_jobs.v1",
    "esi-characters.read_blueprints.v1",
    "esi-corporations.read_blueprints.v1",
    "esi-markets.read_character_orders.v1",
    "esi-markets.read_corporation_orders.v1",
    "esi-skills.read_skills.v1",
    "esi-universe.read_structures.v1",  # resolves a Logistik structure_id to its name - see actions.do_resolve_structure_name
    "esi-corporations.read_structures.v1",  # lists a corp's own structures by name, no personal docking history needed
                                             # (unlike esi-universe.read_structures.v1) - needs the Station_Manager
                                             # in-game role, not just Director - see ESIClient.corporation_structures,
                                             # tried first by actions.do_resolve_structure_name
    "esi-markets.structure_markets.v1",  # live C-J order-book check for pricing (production/pricing.py) - a
                                          # character added before this existed needs to be re-added before this
                                          # specific check works for it; falls back to Goonmetrics until then.
]


def list_shared_producer_characters(tm: TokenManager | None = None) -> list[tuple[str, int, str]]:
    """Returns (auth_role, character_id, character_name) for every character
    currently sharing Assets and/or Market Orders with `production` -
    the sharing-based replacement for `list_producer_characters` (Known
    gap 4). Used wherever Production live-reads a shared character's own
    data: `do_unlisted_stock`, the Characters/Producers sidebar
    (`do_list_producer_characters`), and `sync_esi`'s own "nothing shared
    yet" guard.

    Not for Group-3 access capabilities (structure name resolution,
    structure market book) - decision 9 says those have no tool dimension
    ("any tool asks the Access layer 'which characters can provide this',
    not 'is this shared with me'") - see `list_capability_characters` for
    those.

    `auth_role` is resolved via the Phase 4 selector (largest-matching-
    scope token, same tie-break as everywhere else), preferring a token
    that can read Assets, falling back to one that can read Market Orders.
    A character who shares but holds no token carrying either scope (needs
    Re-authorize) is omitted, not raised - same "skip, don't abort" shape
    every other partial-failure path in this module already has."""
    from ..esi_data.access import shared_owner_ids
    from ..esi_data.selector import select_auth_role

    tm = tm or TokenManager(OAUTH_CONFIG)
    char_ids = sorted(
        set(shared_owner_ids("assets", "production", "character"))
        | set(shared_owner_ids("market_orders", "production", "character"))
    )
    out = []
    for character_id in char_ids:
        role = (
            select_auth_role(character_id, "esi-assets.read_assets.v1", tokens=tm)
            or select_auth_role(character_id, "esi-markets.read_character_orders.v1", tokens=tm)
        )
        if role is None:
            continue
        record = tm.get_record(role)
        out.append((role, character_id, record.character_name if record else str(character_id)))
    return out


def list_capability_characters(capability_key: str, tm: TokenManager | None = None) -> list[tuple[str, int, str]]:
    """(auth_role, character_id, character_name) for every character with
    `capability_key` ticked and a token carrying its scope. The logic lives in
    locations.access (shared with the ESI sync's structure-name resolution);
    this name stays for the callers that already use it."""
    from ..locations import access

    return access.list_capability_characters(capability_key, tm)


_LIVE_REACTION_ACTIVITY_ID = 9  # see _normalize_activity_id


def _normalize_activity_id(activity_id: int) -> int:
    """Confirmed real CCP data inconsistency: the live ESI industry-jobs
    endpoint (/characters/.../industry/jobs/, /corporations/.../industry/
    jobs/) reports Reaction jobs as activity_id 9, but the SDE's static
    blueprint data (industryActivity*.csv, see production/sde.py and
    constants.ACTIVITY_REACTION) files every Reaction recipe under activity_id
    11 - live jobs never use 11 at all. Left unnormalized, every downstream
    consumer that looks live activity_id up against SDE-keyed data silently
    breaks for reactions: storage.get_product_quantity (sde_blueprint_products
    lookup) returns None so the Industry Jobs tab's Quantity column shows '–',
    constants.ACTIVITY_SLOT_CATEGORY.get(9) returns None so reaction jobs
    never count against a character's used reaction slots on the Character
    Slots tab, and constants.ACTIVITY_JOB_LABELS.get(9) falls back to the
    raw '9' instead of 'Reaction'. Normalizing once here, at ingestion, means
    every other place in the codebase can keep treating 11 as the one and
    only Reaction activity_id."""
    return ACTIVITY_REACTION if activity_id == _LIVE_REACTION_ACTIVITY_ID else activity_id


def _industry_job_rows(jobs: list[dict], installer_names: dict[int, str]) -> list[tuple]:
    return [
        (j["job_id"], _normalize_activity_id(j["activity_id"]), j["blueprint_type_id"], j.get("product_type_id"),
         j["runs"], j.get("output_location_id"), j["status"], j["end_date"], j.get("start_date"),
         j.get("installer_id"), installer_names.get(j.get("installer_id"), str(j.get("installer_id"))))
        for j in jobs
    ]


def corp_roles_for_characters(client: ESIClient, characters: list[tuple[str, int, str]]) -> dict[int, str]:
    """{corporation_id: role} for the first character of each distinct corp
    (see locations.access, where the logic lives)."""
    from ..locations import access

    return access.corp_roles_for_characters(client, characters)


def resolve_structure_ids(
    client: ESIClient, location_ids: set[int], corp_roles: dict[int, str],
    get_characters: Callable[[], list[tuple[str, int, str]]],
) -> dict[int, tuple[Optional[str], Optional[int]]]:
    """Two-tier structure-name resolution, kept for the operator-fallback tier
    of production.actions.do_resolve_structure_name (the tenant's own lookups
    go through locations.resolver, docs/LOCATION_RESOLUTION_PLAN.md). Shares the
    exact same logic as the resolver's corp-list/per-character order instead of a
    second hand-rolled copy:
    1. corporation_structures(corp_id) once per distinct corp in corp_roles
       - returns every structure that corp owns, with its name included, in
       one call, so this alone typically resolves most/all of a tenant's
       own structures.
    2. get_structure_name(location_id) per character, per still-unresolved
       id, stopping at the first character that can see it - the expensive
       fallback, only reached for structures owned by a different corp than
       any registered character's (e.g. a structure a character's own
       assets merely sit inside, owned by someone else).

    `get_characters` is called at most once, and only if tier 1 leaves
    something unresolved - a characters list is itself a DB read
    (list_capability_characters), so a caller whose
    corp-structures tier already resolved everything must not pay for it
    (do_resolve_structure_name already has its characters list in hand
    either way, so it just passes `lambda: characters`).

    Never raises - every ESIError is caught and skipped, matching sync_esi's
    "one failure must not abort the whole sync" contract. Returns only the
    ids it actually resolved; a location_id absent from the result stays
    unresolved and it's the caller's job to record that (a cache write, a
    fallback attempt, ...) - this function itself never touches storage."""
    unresolved = set(location_ids)
    resolved: dict[int, tuple[Optional[str], Optional[int]]] = {}

    for corporation_id, role in corp_roles.items():
        if not unresolved:
            break
        try:
            structures = client.corporation_structures(corporation_id, auth_role=role)
        except ESIError:
            continue
        for structure in structures:
            loc_id = structure.get("structure_id")
            if loc_id in unresolved:
                resolved[loc_id] = (structure.get("name"), structure.get("solar_system_id"))
                unresolved.discard(loc_id)

    if unresolved:
        characters = get_characters()
        for loc_id in list(unresolved):
            for role, character_id, _name in characters:
                try:
                    info = client.get_structure_name(loc_id, auth_role=role)
                except ESIError:
                    continue
                resolved[loc_id] = (info.get("name"), info.get("solar_system_id"))
                break

    return resolved


def sync_esi() -> dict:
    """Guards on sharing, then delegates the real character/corp fetch to
    the orchestrator (`do_sync_for_tool("production")` - per-owner
    parallelism, sequential corp-role claim, freshness, the per-owner
    guard, all live there now, not in this module - see
    docs/ESI_ACCESS_PLAN.md Phase 3). This function's own remaining job is
    the guard. Structure names are resolved by the orchestrator at the end of
    the pass (locations.resolver), not here."""
    from ..esi_data.orchestrator import do_sync_for_tool

    tm = TokenManager(OAUTH_CONFIG)
    # Sharing-based guard (Known gap 4, closed) - "is anyone actually
    # sharing Production data" is a different question from "who can help
    # resolve a structure name" (capability-based, below).
    if not list_shared_producer_characters(tm):
        raise ActionError(
            "No Production character shared yet. Share Assets (and the other Production kinds you need) on the Characters page."
        )
    # Structure names are resolved by the orchestrator at the end of the pass
    # (locations.resolver), for every tool, not here.
    return do_sync_for_tool("production")

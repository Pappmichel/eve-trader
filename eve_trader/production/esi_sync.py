"""Syncs character + corp assets/industry-jobs/blueprints from ESI into local
cache tables (see storage.py), so engine.py can compute current stock instead
of relying on manual entry alone.

Fetch is `eve_trader.esi_data.orchestrator.do_sync_for_tool("production")`.
This module keeps character/capability listing (`list_shared_producer_
characters`/`list_capability_characters` - `list_producer_characters` is
superseded, see its own docstring) and opportunistic group-3
structure-name resolution (`_discover_structure_names`). Sequential corp
claim (one corp, members retried in order, Director vs Accountant/Trader
independent) lives in the orchestrator so two characters cannot race to
claim the same corp.
"""
from __future__ import annotations

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
# _discover_structure_names below to decide what's even worth attempting to
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


def list_producer_characters(tm: TokenManager | None = None) -> list[tuple[str, int, str]]:
    """Returns (role_key, character_id, character_name) for every registered
    producer character, e.g. [("producer:2112625428", 2112625428, "Some Character")].

    Superseded (docs/ESI_ACCESS_PLAN.md Known gap 4, closed): every caller
    this module used to have has moved to `list_shared_producer_characters`/
    `list_capability_characters` below, which key off `esi_sharing`/
    `esi_character_capabilities` instead of the legacy `producer:` token
    prefix - a character added via the Characters page's own add-a-
    character path (`esi:<id>`, gap 1) never holds a `producer:` token, so
    this listing is permanently blind to them regardless of sharing. Kept
    only because deleting a function with real test coverage on a whim is
    its own risk; do not add a new caller of this one.

    Uses get_record (no refresh), not get_token - a dead refresh token
    (revoked access, re-registered SSO app, ...) must not take the whole
    list down."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    out = []
    for role in tm.list_roles(PRODUCTION_ROLE_PREFIX):
        record = tm.get_record(role)
        if record is not None:
            out.append((role, record.character_id, record.character_name))
    return out


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
    """Returns (auth_role, character_id, character_name) for every character
    with `capability_key` ticked (Access section, Characters page) and a
    token that actually carries its scope - the capability-based
    counterpart to `list_shared_producer_characters` (Known gap 4). Group 3
    has no tool dimension (decision 9), so this is gated on
    `esi_character_capabilities`, not `esi_sharing`. Used by
    `do_resolve_structure_name`, `pricing.home_prices`/`jita_prices`
    (`structure_market_book`), and `sync_esi`'s opportunistic structure-name
    discovery (`structure_name_resolution`).

    A character with the capability ticked but no token carrying its scope
    (needs Re-authorize) is omitted, not raised, same as
    `list_shared_producer_characters`. Raises `ValueError` for an unknown
    `capability_key` - a typo here is a programming error, not a runtime
    "nothing shared yet" case."""
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


def _discover_structure_names(client: ESIClient, all_assets: list[dict], corp_roles: dict[int, str]) -> dict:
    """Proactive bulk fill of storage.structure_names, driven entirely by
    this sync's own already-fetched asset data - no extra ESI calls to
    *discover* location_ids, since character_assets/corporation_assets
    already return location_id on every asset row (including corp Office
    folder entries - there's no separate ESI "offices" endpoint, offices are
    just root-level asset rows at a station). Only location_ids not already
    cached (storage.get_cached_structure_names) are attempted - this is
    meant to be cheap and incremental every sync tick, not a forced
    re-resolve (use POST resolve-structure-name with force=True for that).

    Two-tier resolution, same preference order as do_resolve_structure_name:
    1. corporation_structures(corp_id) once per distinct corp in corp_roles
       - returns every structure that corp owns, with its name included, in
       one call, so this alone typically resolves most/all of a tenant's
       own structures.
    2. get_structure_name(location_id) per character, per still-unresolved
       ID, stopping at the first character that can see it - the expensive
       fallback, only reached for structures owned by a different corp than
       any registered character's (e.g. a structure a character's own
       assets merely sit inside, owned by someone else).

    Never raises - every ESIError is caught and skipped, matching sync_esi's
    "one failure must not abort the whole sync" contract; an unresolved ID
    just stays unresolved for next sync's retry."""
    candidate_ids = {a["location_id"] for a in all_assets if a["location_id"] >= STRUCTURE_ID_MIN}
    if not candidate_ids:
        return {"candidates": 0, "resolved": 0}

    cached = storage.get_cached_structure_names(list(candidate_ids))
    unresolved = {loc_id for loc_id in candidate_ids if not cached[loc_id][0]}
    if not unresolved:
        return {"candidates": len(candidate_ids), "resolved": 0}

    resolved_count = 0

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
                storage.set_cached_structure_name(loc_id, structure.get("name"), structure.get("solar_system_id"))
                unresolved.discard(loc_id)
                resolved_count += 1

    if unresolved:
        # structure_name_resolution capability, not producer sharing - see
        # list_capability_characters' own docstring (Known gap 4, closed).
        characters = list_capability_characters("structure_name_resolution")
        for loc_id in list(unresolved):
            name = None
            solar_system_id = None
            for role, character_id, _ in characters:
                try:
                    info = client.get_structure_name(loc_id, auth_role=role)
                    name = info.get("name")
                    solar_system_id = info.get("solar_system_id")
                    break
                except ESIError:
                    continue
            storage.set_cached_structure_name(loc_id, name, solar_system_id)
            if name is not None:
                resolved_count += 1

    return {"candidates": len(candidate_ids), "resolved": resolved_count}


def sync_esi() -> dict:
    """Guards on sharing, then delegates the real character/corp fetch to
    the orchestrator (`do_sync_for_tool("production")` - per-owner
    parallelism, sequential corp-role claim, freshness, the per-owner
    guard, all live there now, not in this module - see
    docs/ESI_ACCESS_PLAN.md Phase 3). This function's own remaining job is
    the guard, plus opportunistic group-3 structure-name resolution
    (`_discover_structure_names`) afterward - not an orchestrator kind,
    since it has no freshness/sharing dimension of its own (decision 9)."""
    from ..esi_data.orchestrator import do_sync_for_tool

    tm = TokenManager(OAUTH_CONFIG)
    # Sharing-based guard (Known gap 4, closed) - "is anyone actually
    # sharing Production data" is a different question from "who can help
    # resolve a structure name" (capability-based, below).
    if not list_shared_producer_characters(tm):
        raise ActionError(
            "No Production character shared yet. Share Assets (and the other Production kinds you need) on the Characters page."
        )
    result = do_sync_for_tool("production")
    client = ESIClient(tokens=tm)
    location_ids: list[dict] = []
    with storage.connect() as conn:
        for loc_id, in conn.execute("SELECT DISTINCT location_id FROM character_assets"):
            location_ids.append({"location_id": loc_id})
        for loc_id, in conn.execute("SELECT DISTINCT location_id FROM corp_assets"):
            location_ids.append({"location_id": loc_id})
    # structure_name_resolution capability characters, not producer sharing
    # (Known gap 4, closed) - a character resolves structure names for
    # Production without ever sharing Assets/Market Orders with it.
    corp_roles: dict[int, str] = {}
    for role, character_id, _name in list_capability_characters("structure_name_resolution", tm):
        try:
            corporation_id = client.character_public_info(character_id)["corporation_id"]
        except ESIError:
            continue
        corp_roles.setdefault(corporation_id, role)
    result["structure_names"] = _discover_structure_names(client, location_ids, corp_roles)
    return result

"""Syncs character + corp assets/industry-jobs/blueprints from ESI into local
cache tables (see storage.py), so engine.py can compute current stock instead
of relying on manual entry alone.

Fetch is `eve_trader.esi_data.orchestrator.do_sync_for_tool("production")`.
This module keeps `list_producer_characters` (sidebar + token listing until
Phase 4) and opportunistic group-3 structure-name resolution
(`_discover_structure_names`). Sequential corp claim (one corp, members
retried in order, Director vs Accountant/Trader independent) lives in the
orchestrator so two characters cannot race to claim the same corp.
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

    Uses get_record (no refresh), not get_token - this runs on every
    "characters" sidebar render and at the top of every sync_esi() call, so
    one character with a dead refresh token (revoked access, re-registered
    SSO app, ...) must not take the whole list down. sync_esi() itself
    already refreshes/uses each token independently, per-character, inside
    its own try/except (see its docstring) - that's the right place for a
    refresh failure to surface as "skipped", not here."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    out = []
    for role in tm.list_roles(PRODUCTION_ROLE_PREFIX):
        record = tm.get_record(role)
        if record is not None:
            out.append((role, record.character_id, record.character_name))
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
        characters = list_producer_characters()
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
    """Pulls assets/industry jobs/blueprints/sell orders/skills for every
    registered producer character, plus each character's corp-level data
    (Director role for assets/jobs/blueprints, Accountant/Trader for orders -
    tracked independently per corp, see the comment below). Every ESI call
    is wrapped in its own try/except: one character missing a scope, or one
    corp lacking a role, must not abort the whole sync - it's reported per-
    character/per-corp in the returned dict (e.g. "skipped (re-add
    character?)") instead of raised, so a partial sync still updates
    whatever data it *could* fetch rather than updating nothing at all.

    Two phases: Phase A (_fetch_character_data, parallelized across every
    registered character - see its own docstring) fetches everything that's
    independent per character. Phase B (below, sequential, in the same
    original per-character order) handles corp-level data, which stays
    sequential deliberately - unlike Phase A, it's stateful *across*
    characters (only fetch a given corp's assets/jobs/blueprints/orders
    once, retried with the *next* character sharing that corp if an earlier
    one lacked the Director/Accountant/Trader role - "if several of your
    characters share a corp, it's retried with each one in turn until it
    succeeds", see this module's own top docstring) - parallelizing that
    retry-until-success sequencing would risk two characters racing to
    "claim" the same corp at once. Corp count is typically small (a handful
    at most, regardless of how many characters are registered), so this
    sequential half is cheap either way - almost all of sync_esi()'s wall
    time scales with character *count*, which Phase A already parallelizes.

    Phase C (_discover_structure_names) is still opportunistic group-3
    name resolution after the orchestrator writes assets — not an
    orchestrator kind."""
    from ..esi_data.orchestrator import do_sync_for_tool

    tm = TokenManager(OAUTH_CONFIG)
    characters = list_producer_characters(tm)
    if not characters:
        raise ActionError(
            "No producer character logged in yet. Use 'Add Character' in the sidebar."
        )
    result = do_sync_for_tool("production")
    client = ESIClient(tokens=tm)
    location_ids: list[dict] = []
    with storage.connect() as conn:
        for loc_id, in conn.execute("SELECT DISTINCT location_id FROM character_assets"):
            location_ids.append({"location_id": loc_id})
        for loc_id, in conn.execute("SELECT DISTINCT location_id FROM corp_assets"):
            location_ids.append({"location_id": loc_id})
    corp_roles: dict[int, str] = {}
    for role, character_id, _name in characters:
        try:
            corporation_id = client.character_public_info(character_id)["corporation_id"]
        except ESIError:
            continue
        corp_roles.setdefault(corporation_id, role)
    result["structure_names"] = _discover_structure_names(client, location_ids, corp_roles)
    return result

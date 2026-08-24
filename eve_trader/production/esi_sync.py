"""Syncs character + corp assets/industry-jobs/blueprints from ESI into local
cache tables (see storage.py), so engine.py can compute current stock instead
of relying on manual entry alone.

Supports multiple characters: each is authorized separately (TokenManager.
get_token_interactive_multi stores them as "producer:<character_id>") and
sync_esi() pulls all of them, merging into one combined asset/job/blueprint
picture (item_id is globally unique across characters, so merging is safe).
Corp-level calls need that character to hold the Director role in-game; if
none of your registered characters in a given corp have it, that corp's data
is skipped (not fatal). If several of your characters share a corp, it's
retried with each one in turn until it succeeds - so if you add both a
Director and a non-Director alt from the same corp, tracking works regardless
of which one you added first.

There's no such thing as authorizing a corporation directly in EVE's SSO -
corp-level scopes are always granted through a member character. To track a
corp, add a character (via get_token_interactive_multi) who belongs to it and
holds the Director role for the endpoints that need it.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from .constants import ACTIVITY_REACTION, job_slots_from_skills

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


def _asset_rows(assets: list[dict]) -> list[tuple]:
    return [
        (a["item_id"], a["type_id"], a["location_id"], a["location_flag"],
         a["quantity"], int(bool(a.get("is_blueprint_copy"))), a.get("owner_name"))
        for a in assets
    ]


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


def _blueprint_rows(bps: list[dict]) -> list[tuple]:
    return [
        (b["item_id"], b["type_id"], b["location_id"], b["location_flag"], b["quantity"],
         b["material_efficiency"], b["time_efficiency"], b["runs"])
        for b in bps
    ]


def _sell_order_rows(orders: list[dict], character_name: str) -> list[tuple]:
    return [
        (o["order_id"], o["type_id"], o["location_id"], o["region_id"], o["volume_remain"], character_name)
        for o in orders if not o.get("is_buy_order")
    ]


def _fetch_character_data(client: ESIClient, role: str, character_id: int, character_name: str) -> dict:
    """Everything about ONE character that doesn't depend on any *other*
    character - own assets/jobs/blueprints/skills/orders, plus (read-only,
    doesn't fetch anything corp-level itself) which corp they belong to.
    This is the independent, parallelizable half of sync_esi()'s per-
    character work (see sync_esi's own docstring for why the corp-level
    half stays sequential) - confirmed real gap (2026-08-16): this used to
    run strictly one character at a time even though each character's own
    ESI calls don't depend on any other character's at all, purely network-
    latency-bound (18 registered characters x up to 6 sequential ESI calls
    each, confirmed real account data) - a textbook case for
    ThreadPoolExecutor, same "independent, latency-bound work" shape
    goonmetrics_client.py's price_history_chunked and esi_client.py's
    _get_all_pages already use.

    Sharing one ESIClient (and its one requests.Session) across threads here
    is the same pattern goonmetrics_client.py's price_history_chunked
    already relies on for the same reason; ESI's error-limit budget
    (ESIClient._error_limit_lock) and TokenManager's refresh-on-expiry path
    (TokenManager._refresh_lock) are both already class-level-locked
    specifically to support concurrent callers - this isn't a new
    thread-safety requirement, just the first caller to actually exercise
    it with more than one thread."""
    per_character: dict = {}
    assets: list[dict] = []
    jobs: list[dict] = []
    bps: list[dict] = []
    sell_rows: list[tuple] = []
    slot_row: Optional[tuple] = None
    corporation_id: Optional[int] = None
    corp_name: Optional[str] = None

    try:
        assets = client.character_assets(character_id, auth_role=role)
        jobs = client.character_industry_jobs(character_id, auth_role=role)
        bps = client.character_blueprints(character_id, auth_role=role)
    except ESIError as e:
        return {
            "character_name": character_name, "role": role, "character_id": character_id,
            "assets": [], "jobs": [], "bps": [], "sell_rows": [], "slot_row": None,
            "corporation_id": None, "corp_name": None, "per_character": f"Error: {e}",
        }
    for a in assets:
        a["owner_name"] = character_name
    per_character = {"assets": len(assets), "industry_jobs": len(jobs), "blueprints": len(bps)}

    try:
        skills = client.character_skills(character_id, auth_role=role)
        levels = {s["skill_id"]: s["active_skill_level"] for s in skills.get("skills", [])}
        slots = job_slots_from_skills(levels)
        slot_row = (character_name, slots["manufacturing"], slots["reaction"], slots["science"])
        per_character["slots"] = slots
    except ESIError as e:
        # Separate scope (esi-skills.read_skills.v1) - a character added
        # before this scope existed won't have it until re-added.
        per_character["slots"] = f"skipped (re-add character? {e})"

    try:
        orders = client.character_orders(character_id, auth_role=role)
        sell_rows = _sell_order_rows(orders, character_name)
        per_character["sell_orders"] = len(sell_rows)
    except ESIError as e:
        # Separate scope (esi-markets.read_character_orders.v1) - a character
        # added before this scope existed won't have it until re-added.
        per_character["sell_orders"] = f"skipped (re-add character? {e})"

    try:
        corporation_id = client.character_public_info(character_id)["corporation_id"]
        corp_name = client.corporation_public_info(corporation_id).get("name", str(corporation_id))
    except ESIError as e:
        per_character["corp"] = f"skipped (public info fetch failed: {e})"

    return {
        "character_name": character_name, "role": role, "character_id": character_id,
        "assets": assets, "jobs": jobs, "bps": bps, "sell_rows": sell_rows, "slot_row": slot_row,
        "corporation_id": corporation_id, "corp_name": corp_name, "per_character": per_character,
    }


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

    Phase C (_discover_structure_names, after Phase B) proactively resolves
    any not-yet-cached structure IDs found in this sync's own asset data -
    additive and non-fatal like everything else here, never affects whether
    the rest of the sync succeeds."""
    tm = TokenManager(OAUTH_CONFIG)
    characters = list_producer_characters(tm)
    if not characters:
        raise ActionError(
            "No producer character logged in yet. Use 'Add Character' in the sidebar."
        )
    client = ESIClient(tokens=tm)

    # storage.with_current_tenant: pool workers don't inherit contextvars
    # from this thread - if a character's token happens to be expired right
    # now, _fetch_character_data's ESI calls would otherwise refresh it from
    # inside a worker thread with no ambient tenant set, 500ing with "no
    # current tenant set" (see storage.with_current_tenant's own docstring).
    with ThreadPoolExecutor(max_workers=min(8, len(characters))) as pool:
        # list(), not as_completed() - preserves `characters`' original order
        # so Phase B's "first character in the original list order claims
        # this corp" behavior is unchanged from the old sequential version.
        char_results = list(pool.map(
            storage.with_current_tenant(lambda c: _fetch_character_data(client, c[0], c[1], c[2])), characters,
        ))

    all_char_assets: list[dict] = []
    all_char_jobs: list[dict] = []
    all_char_bps: list[dict] = []
    all_sell_orders: list[tuple] = []
    all_corp_assets: list[dict] = []
    all_corp_jobs: list[dict] = []
    all_corp_bps: list[dict] = []
    slot_rows: list[tuple] = []

    per_character: dict = {}
    per_corporation: dict = {}
    corp_orders_done: set[str] = set()
    corp_roles: dict[int, str] = {}

    for r in char_results:
        character_name = r["character_name"]
        per_character[character_name] = r["per_character"]
        all_char_assets.extend(r["assets"])
        all_char_jobs.extend(r["jobs"])
        all_char_bps.extend(r["bps"])
        all_sell_orders.extend(r["sell_rows"])
        if r["slot_row"] is not None:
            slot_rows.append(r["slot_row"])

        corporation_id, corp_name, role = r["corporation_id"], r["corp_name"], r["role"]
        if corporation_id is None:
            continue  # this character's own public-info fetch failed - already recorded above

        # First-registered-character-per-corp wins, tried once by
        # _discover_structure_names below - corporation_structures needs
        # Station_Manager specifically (not Director), so a character
        # without it just gets an ESIError there, non-fatal, same as every
        # other per-corp call in this loop.
        corp_roles.setdefault(corporation_id, role)

        # Assets/jobs/blueprints need the Director role; orders need
        # Accountant or Trader instead - tracked independently (not as one
        # all-or-nothing block) so a character with one role but not the
        # other still contributes whichever half it can, and a later
        # character in the same corp can still fill in the other half.
        if not isinstance(per_corporation.get(corp_name), dict):
            try:
                corp_assets = client.corporation_assets(corporation_id, auth_role=role)
                corp_jobs = client.corporation_industry_jobs(corporation_id, auth_role=role)
                corp_bps = client.corporation_blueprints(corporation_id, auth_role=role)
            except ESIError as e:
                # Don't give up on this corp for good - a later character in the
                # loop might hold the Director role this one doesn't.
                per_corporation[corp_name] = f"skipped for {character_name} (missing Director role? {e})"
            else:
                for a in corp_assets:
                    a["owner_name"] = f"{corp_name} (corp)"
                all_corp_assets.extend(corp_assets)
                all_corp_jobs.extend(corp_jobs)
                all_corp_bps.extend(corp_bps)
                per_corporation[corp_name] = {
                    "assets": len(corp_assets), "industry_jobs": len(corp_jobs), "blueprints": len(corp_bps),
                }

        if corp_name not in corp_orders_done:
            try:
                corp_orders = client.corporation_orders(corporation_id, auth_role=role)
            except ESIError:
                pass  # a later character in this corp might hold Accountant/Trader
            else:
                corp_orders_done.add(corp_name)
                sell_rows = _sell_order_rows(corp_orders, f"{corp_name} (corp)")
                all_sell_orders.extend(sell_rows)
                if isinstance(per_corporation.get(corp_name), dict):
                    per_corporation[corp_name]["corp_sell_orders"] = len(sell_rows)

    structure_names = _discover_structure_names(client, all_char_assets + all_corp_assets, corp_roles)

    installer_ids = {j.get("installer_id") for j in all_char_jobs + all_corp_jobs if j.get("installer_id")}
    installer_names = client.resolve_names(list(installer_ids))

    storage.replace_assets("character_assets", _asset_rows(all_char_assets))
    storage.replace_industry_jobs("character_industry_jobs", _industry_job_rows(all_char_jobs, installer_names))
    storage.replace_blueprints("character_blueprints", _blueprint_rows(all_char_bps))
    storage.replace_sell_orders(all_sell_orders)
    storage.replace_assets("corp_assets", _asset_rows(all_corp_assets))
    storage.replace_industry_jobs("corp_industry_jobs", _industry_job_rows(all_corp_jobs, installer_names))
    storage.replace_blueprints("corp_blueprints", _blueprint_rows(all_corp_bps))
    storage.replace_character_slots(slot_rows)

    return {"characters": per_character, "corporations": per_corporation, "structure_names": structure_names}

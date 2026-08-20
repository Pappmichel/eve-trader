"""Production tool actions - UI-agnostic entry points, same shape as the
trading tool's actions.py, so the FastAPI routers (eve_trader/api/routers/)
only ever call into this module (no direct storage/engine access from the UI
layer).
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import ConfigError, OAUTH_CONFIG, save_tenant_config_overrides
from ..esi_client import ESIClient, ESIError
from ..goonmetrics_client import GoonmetricsClient
from . import esi_sync, invention, jobs, pricing, sde
from .config import PRODUCTION_CONFIG, ProductionConfig, validate_production_overrides
from .constants import DECRYPTORS, JOB_CATEGORIES
from .engine import (
    build_material_tree, discover_build_candidates, discover_ship_margins, item_margin_detail,
    invalidate_discover_cache, invalidate_ship_margin_cache,
    invalidate_production_locations_cache, market_status, plan_asset_optimized, plan_production, stock_value,
)
from .models import AssetLocationRow, BuildCandidate, OwnedBlueprintRow, ShipMarginRow, UnlistedStockRow


def do_update_settings(updates: dict, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Persists `updates` to tenant_settings and applies them to the live
    PRODUCTION_CONFIG immediately (see Settings tab)."""
    try:
        validate_production_overrides(updates)  # structure_type/rig_tier enum check - see its docstring
        save_tenant_config_overrides("production", updates, cfg, cfg_type=ProductionConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    invalidate_discover_cache()  # a settings change (margin gate, structure/rig, ...) can change the result set
    invalidate_ship_margin_cache()  # structure/rig/fee settings feed build_cost/margins here too
    if "home_location_id" in updates:
        invalidate_production_locations_cache()  # _current_stock's location set includes this
    return updates


def do_set_system(profile: str, system_name: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Resolves `system_name` (e.g. "Jita", "Amarr") to its solar system ID via
    ESI once, then persists both under the given `profile` ("component" or
    "manufacturing" - see production/constants.py COMPONENT_GROUP_IDS) - so the
    rest of the app (system cost index lookups) keeps using the numeric ID
    without re-resolving it every time."""
    if profile not in ("component", "manufacturing"):
        raise ActionError(f"Unknown profile '{profile}'. Options: component, manufacturing")
    system_name = system_name.strip()
    try:
        system_id = ESIClient().resolve_system_id(system_name)
    except ESIError as e:
        # A transient ESI error here used to become a raw, unhandled 500 (the
        # API router's _wrap only catches ActionError) - convert to the app's
        # normal clean error path like every other ESI call site in this file.
        raise ActionError(f"Could not resolve '{system_name}' via ESI right now: {e}") from e
    if system_id is None:
        raise ActionError(f"Solar system '{system_name}' not found. Exact name?")
    save_tenant_config_overrides(
        "production", {f"{profile}_system_id": system_id, f"{profile}_system_name": system_name},
        cfg, cfg_type=ProductionConfig,
    )
    invalidate_discover_cache()  # system cost index feeds directly into build cost
    invalidate_ship_margin_cache()
    return {f"{profile}_system_name": system_name, f"{profile}_system_id": system_id}


def do_refresh_sde() -> dict:
    """Downloads and caches the current Fuzzwork SDE export."""
    try:
        result = sde.refresh_sde()
    except requests.RequestException as e:
        # Confirmed real gap: sde.refresh_sde()'s network errors used to
        # reach the router unconverted - a raw 500 from POST /sde/refresh
        # instead of the ActionError every other ESI/Goonmetrics-touching
        # action in this module converts a network failure to.
        raise ActionError(f"SDE refresh failed: {e}") from e
    invalidate_discover_cache()  # the item/blueprint universe itself may have changed
    invalidate_ship_margin_cache()
    return result


def do_check_sde_freshness(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Two independent staleness checks, both read-only/no-op (neither ever
    auto-refreshes anything):

    1. newer_sde_available - is there a newer Fuzzwork dump than the one
       currently cached (see sde.check_for_newer_sde)?
    2. trading_universe_stale - has the SDE cache itself been refreshed more
       recently than Trading's "Load Market Groups" candidate_universe
       snapshot was last rebuilt? Production's own scans (discover_build_
       candidates, plan_production) always walk the live SDE cache directly,
       so they never need this - but Trading caches a snapshot (see
       actions.do_build_universe), and a new item added to the SDE isn't
       visible there until that snapshot is explicitly rebuilt. True whenever
       both timestamps are known and the SDE is newer; False (not "unknown")
       if either side has never run at all - nothing to compare yet."""
    freshness = sde.check_for_newer_sde(cfg)
    sde_state = storage.get_sde_refresh_state()
    sde_refreshed_at = sde_state[0] if sde_state else None
    universe_built_at = storage.get_candidate_universe_built_at()
    # Timestamps come from two different isoformat() calls (this module's own
    # tz-aware datetime.now(timezone.utc).isoformat() vs Trading's actions.
    # now_ts(), a naive utcnow().isoformat(timespec="seconds")) - comparing
    # the full strings directly risks a spurious mismatch from the trailing
    # "+00:00"/microseconds Trading's format lacks, so compare only the
    # shared whole-second prefix both formats guarantee.
    trading_universe_stale = bool(
        sde_refreshed_at and universe_built_at and sde_refreshed_at[:19] > universe_built_at[:19]
    )
    return {**freshness, "trading_universe_stale": trading_universe_stale, "trading_universe_built_at": universe_built_at}


# No do_auth_add_producer_character() here, deliberately - removed (real bug,
# confirmed live via the identical Doctrine-tool code path): a producer
# character logs in via the redirect-based EVE SSO flow buyer/seller already
# use (GET /api/auth/producer/start -> EVE SSO -> /api/auth/callback, see
# api/routers/auth.py's _scopes_for and ProductionLayout.tsx), never via
# TokenManager.get_token_interactive_multi's old server-side
# webbrowser.open()-plus-local-HTTP-server flow, which binds its own
# listener on the exact host:port the running backend already uses - works
# only by accident in local dev, always fails on a real deployment.


def do_list_producer_characters() -> list[tuple[str, int, str]]:
    return esi_sync.list_producer_characters()


def do_remove_producer_character(role_key: str) -> dict:
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


def do_sync_esi() -> dict:
    """Pulls character + corp assets/industry-jobs/blueprints from ESI for
    every registered producer character."""
    result = esi_sync.sync_esi()
    storage.set_esi_sync_time("production", datetime.now(timezone.utc).isoformat())
    # Owned-BPO ME/TE (storage.get_owned_bpo_best_me_te, used by _owned_bpo_mods
    # for every Tech I build-cost calc) comes from character_blueprints/
    # corp_blueprints - exactly what this just refreshed. Covers every case
    # that can change it: a newly added producer character's first sync, a
    # researched BPO's ME/TE improving, or a BPO changing hands - not just
    # add/remove-character, which by itself changes no stored blueprint data
    # at all (do_auth_add_producer_character only gets a token;
    # do_remove_producer_character only deletes it - storage.character_
    # blueprints/corp_blueprints keep whatever was last synced either way,
    # until the next do_sync_esi call, which is this one).
    invalidate_discover_cache()
    invalidate_ship_margin_cache()  # owned-BPO ME/TE feeds Tech I build_cost, including for ships
    return result


def do_get_esi_sync_time() -> dict:
    return {"synced_at": storage.get_esi_sync_time("production")}


def do_add_stock_target(type_name: str, backup_stock: float = 0,
                         home_market_stock: float | None = None, jita_market_stock: float | None = None) -> dict:
    matches = storage.search_sde_types(type_name, limit=2)
    exact = [m for m in matches if m[1].lower() == type_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{type_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{type_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]
    storage.upsert_stock_target(type_id, resolved_name, backup_stock, home_market_stock, jita_market_stock)
    invalidate_discover_cache()  # this item must now be excluded from discovery (already tracked)
    return {"type_id": type_id, "type_name": resolved_name, "backup_stock": backup_stock,
            "home_market_stock": home_market_stock, "jita_market_stock": jita_market_stock}


def do_update_stock_target(type_id: int, backup_stock: float | None = None,
                            home_market_stock: float | None = None, jita_market_stock: float | None = None) -> dict:
    """Edits an *existing* stock target's numeric fields in place (GitHub
    issue #16 - "should be able to change the targets directly in the table,
    like the targets on the doctrine table"). Unlike do_add_stock_target,
    takes type_id directly (the row already exists - no name lookup/exact-
    match ambiguity to resolve) and each field is genuinely optional:
    storage.upsert_stock_target already treats None as "leave this field
    alone" (see its own docstring), so editing just one column in the table
    doesn't require also resending the other two's current values.

    Doesn't invalidate the discover-build-candidates cache (unlike add/
    remove) - editing an existing target's numbers doesn't change *which*
    items count as "already tracked" (the only thing that cache depends
    on), only add/remove/SDE-refresh/decryptor-change do."""
    if (backup_stock is not None and backup_stock < 0) or (home_market_stock is not None and home_market_stock < 0) \
            or (jita_market_stock is not None and jita_market_stock < 0):
        raise ActionError("Stock targets cannot be negative.")
    sde_type = storage.get_sde_type(type_id)
    if sde_type is None:
        raise ActionError(f"Unknown type_id {type_id} - refresh SDE first?")
    type_name = sde_type[2]
    storage.upsert_stock_target(type_id, type_name, backup_stock, home_market_stock, jita_market_stock)
    return {"type_id": type_id, "type_name": type_name, "backup_stock": backup_stock,
            "home_market_stock": home_market_stock, "jita_market_stock": jita_market_stock}


def do_remove_stock_target(type_id: int) -> dict:
    storage.delete_stock_target(type_id)
    invalidate_discover_cache()  # this item is eligible for discovery again
    return {"removed": type_id}


def do_set_manual_stock(type_id: int, count: float) -> dict:
    # The UI's NumberInput already enforces min=0, but the API itself had no
    # boundary check (confirmed: a raw negative POST was silently accepted) -
    # a negative override would poison _current_stock for that item across
    # every stock-target/plan/stock-value computation that reads it.
    if count < 0:
        raise ActionError("Current stock cannot be negative.")
    storage.upsert_manual_stock(type_id, count)
    return {"type_id": type_id, "count": count}


def do_set_manual_build_buy(type_id: int, decision: str) -> dict:
    storage.upsert_manual_build_buy(type_id, decision)
    return {"type_id": type_id, "decision": decision}


def do_clear_manual_build_buy(type_id: int) -> dict:
    storage.delete_manual_build_buy(type_id)
    return {"type_id": type_id, "decision": "Auto"}


def do_set_selected_decryptor(type_id: int, decryptor: str) -> dict:
    if decryptor not in DECRYPTORS:
        raise ActionError(f"Unknown decryptor '{decryptor}'. Options: {', '.join(DECRYPTORS)}")
    storage.upsert_selected_decryptor(type_id, decryptor)
    invalidate_discover_cache()  # changes this item's T2 build cost/margin
    invalidate_ship_margin_cache()  # T2 ships are affected too
    return {"type_id": type_id, "decryptor": decryptor}


def do_clear_selected_decryptor(type_id: int) -> dict:
    storage.delete_selected_decryptor(type_id)
    invalidate_discover_cache()
    invalidate_ship_margin_cache()
    return {"type_id": type_id, "decryptor": "Best"}


def do_set_category_location(category: str, location_id: int) -> dict:
    if category not in JOB_CATEGORIES:
        raise ActionError(f"Unknown category '{category}'. Options: {', '.join(JOB_CATEGORIES)}")
    storage.upsert_category_location(category, location_id)
    # Also remember it as a quick-switch option - setting a category active
    # shouldn't require a separate "save as option" step too.
    storage.add_category_location_option(category, location_id)
    invalidate_production_locations_cache()  # _current_stock now checks this location too
    return {"category": category, "location_id": location_id}


def do_clear_category_location(category: str) -> dict:
    storage.delete_category_location(category)
    invalidate_production_locations_cache()
    return {"category": category}


def do_add_category_location_option(category: str, location_id: int) -> dict:
    if category not in JOB_CATEGORIES:
        raise ActionError(f"Unknown category '{category}'. Options: {', '.join(JOB_CATEGORIES)}")
    storage.add_category_location_option(category, location_id)
    return {"category": category, "location_id": location_id}


def do_remove_category_location_option(category: str, location_id: int) -> dict:
    storage.delete_category_location_option(category, location_id)
    return {"category": category, "location_id": location_id}


def do_resolve_structure_name(location_id: int, force: bool = False) -> dict:
    """Resolves `location_id` to its structure name (and solar_system_id,
    GitHub issue #12 - see storage.load_category_system_ids) via ESI, cached
    indefinitely (storage.get/set_cached_structure_name) unless `force`.

    Tries two paths, in order:
    1. Each registered character's corp's structure list
       (ESIClient.corporation_structures - esi-corporations.read_structures.v1
       + Station_Manager role) - covers every structure that corp owns, with
       no dependency on any one character having personally docked there.
       Each distinct corporation is only queried once.
    2. Per-character docking history (ESIClient.get_structure_name -
       esi-universe.read_structures.v1) - tries every registered producer
       character in turn until one can actually "see" the structure (needs
       docking rights/to have visited it - a random character's token isn't
       guaranteed access to every structure, same fallback esi_sync.py uses
       for corp-level calls). Only reached if path 1 didn't resolve it (e.g.
       the structure belongs to a different corp than any registered
       character's, or no registered character holds Station_Manager).

    A character added before esi-universe.read_structures.v1/
    esi-corporations.read_structures.v1 existed needs to be re-added (remove +
    add again) before either path works for it."""
    if not force:
        was_cached, cached_name = storage.get_cached_structure_name(location_id)
        if was_cached:
            return {"location_id": location_id, "name": cached_name, "cached": True}

    characters = esi_sync.list_producer_characters()
    if not characters:
        raise ActionError("No producer character logged in yet.")

    client = ESIClient(tokens=TokenManager(OAUTH_CONFIG))
    name = None
    solar_system_id = None

    tried_corporations: set[int] = set()
    for role, character_id, character_name in characters:
        try:
            corporation_id = client.character_public_info(character_id)["corporation_id"]
        except ESIError:
            continue
        if corporation_id in tried_corporations:
            continue
        tried_corporations.add(corporation_id)
        try:
            structures = client.corporation_structures(corporation_id, auth_role=role)
        except ESIError:
            continue  # this character lacks Station_Manager (or the scope) - try the next one
        for structure in structures:
            if structure.get("structure_id") == location_id:
                name = structure.get("name")
                solar_system_id = structure.get("solar_system_id")
                break
        if name:
            break

    if name is None:
        for role, character_id, character_name in characters:
            try:
                info = client.get_structure_name(location_id, auth_role=role)
                name = info.get("name")
                solar_system_id = info.get("solar_system_id")
                break
            except ESIError:
                continue  # this character can't see it - try the next one

    storage.set_cached_structure_name(location_id, name, solar_system_id)
    return {"location_id": location_id, "name": name, "cached": False}


def do_estimate_invention(product_name: str, decryptor_name: str | None = None,
                           cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """`product_name` is the T2/T3 blueprint you want (e.g. "Small Shield Booster
    II Blueprint"), not the T1 base blueprint. If `decryptor_name` is None,
    compares all decryptor options; otherwise estimates just that one."""
    recipe = storage.find_invention_recipe_by_product_name(product_name.strip())
    if recipe is None:
        raise ActionError(
            f"No invention recipe found for '{product_name}'. Exact name? Refresh SDE first?"
        )
    t1_blueprint_type_id, product_blueprint_id = recipe

    gm_client = GoonmetricsClient()
    home = pricing.home_prices(gm_client, cfg)
    jita = pricing.jita_prices(gm_client)
    # activity 1 = Manufacturing: the invented BPC's *own* build materials,
    # used to weigh ME savings the same way the Bauliste does (see engine.py).
    reducible_cost = invention.reducible_material_cost(product_blueprint_id, 1, home, jita, cfg)

    if decryptor_name is None:
        results = invention.compare_decryptors(t1_blueprint_type_id, home, jita, cfg, reducible_cost)
    else:
        if decryptor_name not in DECRYPTORS:
            raise ActionError(f"Unknown decryptor '{decryptor_name}'. Options: {', '.join(DECRYPTORS)}")
        results = [invention.estimate(t1_blueprint_type_id, decryptor_name, home, jita, cfg, reducible_cost)]

    return {"results": results}


def do_refresh_production(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Recomputes Inventory (target vs. current stock) and the resulting
    Buy/Build lists. Raises if the SDE cache is empty (nothing to plan against)."""
    if sum(storage.sde_row_counts().values()) == 0:
        raise ActionError("SDE cache is empty. Run 'Refresh SDE' first.")
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured.")
    plan = plan_production(cfg)
    return {
        "stock_targets": len(plan["inventory"]),
        "missing_types": sum(1 for r in plan["inventory"] if r.total_missing > 0),
        "buy_entries": len(plan["buy_list"]),
        "build_jobs": len(plan["build_list"]),
        "plan": plan,
    }


def do_refresh_asset_plan(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Recomputes the asset-aware Bauliste (engine.plan_asset_optimized).
    Raises under the same conditions as do_refresh_production."""
    if sum(storage.sde_row_counts().values()) == 0:
        raise ActionError("SDE cache is empty. Run 'Refresh SDE' first.")
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured.")
    plan = plan_asset_optimized(cfg)
    return {"jobs": len(plan["jobs"]), "plan": plan}


def do_market_status(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Personal stock vs. backup target, and home/Jita market-listed stock vs.
    their own targets, for the Marktstatus tab."""
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured.")
    return {"rows": market_status(cfg)}


def do_stock_value(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Total ISK value of current stock, for the KPI shown on the
    Stock-Ziele tab."""
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured.")
    return stock_value(cfg)


def do_build_material_tree(type_name: str, quantity: float = 1.0,
                            cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Resolves `type_name` (exact match, same lookup do_add_stock_target uses
    - not just invented T2/T3 items like do_estimate_invention, any
    manufacturable-or-not item) and returns its full recursive material tree.
    See engine.build_material_tree."""
    matches = storage.search_sde_types(type_name, limit=2)
    exact = [m for m in matches if m[1].lower() == type_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{type_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{type_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]

    gm_client = GoonmetricsClient()
    home = pricing.home_prices(gm_client, cfg)
    jita = pricing.jita_prices(gm_client)
    selected_decryptors = storage.load_selected_decryptors()
    t2_memo: dict = {}

    return build_material_tree(type_id, quantity, cfg, home, jita, selected_decryptors, t2_memo)


def do_search_item_locations(item_name: str) -> dict:
    """Resolves `item_name` (exact match, same lookup do_add_stock_target/
    do_build_material_tree use) and returns every station/structure it's
    currently sitting at, who owns it (character or corp), and how much -
    see storage.search_item_stock_locations. Reflects the last "Sync ESI
    Data" run, not a live ESI call."""
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]

    rows = storage.search_item_stock_locations(type_id)
    locations = [
        AssetLocationRow(location_id=location_id, location_name=location_name,
                          owner_name=owner_name, quantity=quantity)
        for location_id, location_name, owner_name, quantity in rows
    ]
    return {"type_id": type_id, "type_name": resolved_name, "locations": locations}


def do_discover_build_candidates(top_n: int = 200, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Scans every manufacturable, market-listed SDE item not already a
    stock target for ones where building clearly beats buying right now -
    see engine.discover_build_candidates. Needs the SDE cache populated
    (Refresh SDE) to find anything at all.

    The underlying scan is cached for a few minutes (engine._DISCOVER_CACHE_TTL) -
    repeat calls (e.g. re-clicking "Discover" with a different top_n) reuse it
    instead of re-walking ~19,400 SDE items each time; invalidated automatically
    by anything that changes the result set (Settings save, stock target
    add/remove, decryptor change, SDE refresh - see invalidate_discover_cache
    call sites in this module)."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Refresh SDE first.")
    candidates = discover_build_candidates(cfg, top_n=top_n)
    return {"rows": [BuildCandidate(**c) for c in candidates]}


def do_get_ship_margins(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Margin page's list view - every ship's current home/Jita price, build
    cost, and both margins. See engine.discover_ship_margins - cached there,
    same as do_discover_build_candidates."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Refresh SDE first.")
    rows = discover_ship_margins(cfg)
    return {"rows": [ShipMarginRow(**r) for r in rows]}


def do_get_item_margin(item_name: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> ShipMarginRow:
    """Margin page's search - resolves `item_name` (exact match, same lookup
    do_build_material_tree/do_search_item_locations use) and returns its
    current home/Jita price, build cost, and both margins for any category,
    not just ships. See engine.item_margin_detail."""
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]

    return ShipMarginRow(**item_margin_detail(type_id, resolved_name, cfg))


def _accumulate_stock_at_location(assets: list[dict], location_id: int, out: dict[int, float]) -> None:
    """In-memory equivalent of storage.esi_stock_at_location's per-location
    aggregation (same corp-office-unwrap + NON_STOCK_LOCATION_FLAGS exclusion
    logic - see that function's docstring for why both are needed), for
    live-fetched asset lists that were never written to the DB."""
    office_item_ids = {
        a["item_id"] for a in assets
        if a.get("type_id") == storage.OFFICE_TYPE_ID and a.get("location_id") == location_id
    }
    valid_locations = {location_id} | office_item_ids
    for a in assets:
        if a.get("location_id") not in valid_locations:
            continue
        if a.get("location_flag") in storage.NON_STOCK_LOCATION_FLAGS:
            continue
        out[a["type_id"]] = out.get(a["type_id"], 0.0) + a.get("quantity", 0)


def do_unlisted_stock(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Live-fetches every registered producer character's (+ their corps')
    assets and sell orders (personal *and* corp - see corp_orders_seen below)
    at cfg.home_location_id, and returns stock targets sitting there with no
    open sell order at all - matches the Trading tool's
    do_check_seller_unlisted_stock (always a fresh live read, never relies
    on the last "ESI-Daten synchronisieren" snapshot, so it can't show a
    stale answer if you sold/listed something moments ago).

    Only considers stock targets that actually have a home_market_stock or
    jita_market_stock target set (see listed_target_ids below) - a target
    with only backup_stock configured is a personal/component buffer never
    meant to be listed for sale, so it must not show up here."""
    if cfg.home_location_id is None:
        raise ActionError("No structure/location ID configured (Settings -> Market & Location).")
    stock_targets = storage.load_stock_targets()
    if not stock_targets:
        raise ActionError("No stock targets configured.")
    characters = esi_sync.list_producer_characters()
    if not characters:
        raise ActionError("No producer character logged in yet.")

    client = ESIClient(tokens=TokenManager(OAUTH_CONFIG))
    stock_qty: dict[int, float] = {}
    sell_qty: dict[int, float] = {}
    seen_corp_assets: set[int] = set()
    # Separate from seen_corp_assets: corp assets need the Director role,
    # corp orders need Accountant/Trader - confirmed real bug where stock
    # sitting in a corp hangar (assets, Director-gated) had a real corp sell
    # order (orders, Accountant/Trader-gated) that this check never looked
    # at, so it was always flagged "unlisted" even while actually for sale.
    seen_corp_orders: set[int] = set()

    for role, character_id, _character_name in characters:
        try:
            assets = client.character_assets(character_id, auth_role=role)
        except ESIError:
            assets = []
        _accumulate_stock_at_location(assets, cfg.home_location_id, stock_qty)

        try:
            orders = client.character_orders(character_id, auth_role=role)
        except ESIError:
            orders = []
        for o in orders:
            if not o.get("is_buy_order") and o.get("location_id") == cfg.home_location_id:
                sell_qty[o["type_id"]] = sell_qty.get(o["type_id"], 0.0) + o.get("volume_remain", 0)

        try:
            corporation_id = client.character_public_info(character_id)["corporation_id"]
        except ESIError:
            continue

        if corporation_id not in seen_corp_assets:
            try:
                corp_assets = client.corporation_assets(corporation_id, auth_role=role)
            except ESIError:
                pass  # missing Director role on this character - a later one might have it
            else:
                seen_corp_assets.add(corporation_id)
                _accumulate_stock_at_location(corp_assets, cfg.home_location_id, stock_qty)

        if corporation_id not in seen_corp_orders:
            try:
                corp_orders = client.corporation_orders(corporation_id, auth_role=role)
            except ESIError:
                pass  # missing Accountant/Trader role on this character - a later one might have it
            else:
                seen_corp_orders.add(corporation_id)
                for o in corp_orders:
                    if not o.get("is_buy_order") and o.get("location_id") == cfg.home_location_id:
                        sell_qty[o["type_id"]] = sell_qty.get(o["type_id"], 0.0) + o.get("volume_remain", 0)

    # Only stock targets with an actual home/Jita *market* target are meant to
    # be listed for sale - a target with only backup_stock set (home_market_
    # stock and jita_market_stock both None/0) is a personal/component buffer
    # the user never intends to sell, so it must not be flagged as "unlisted"
    # (confirmed real bug: e.g. every Decryptor here is backup-only, yet all
    # of them showed up as "unlisted" yielding a useless, noisy list).
    listed_target_ids = {t[0] for t in stock_targets if (t[3] or 0) > 0 or (t[4] or 0) > 0}
    rows = []
    for type_id, qty in stock_qty.items():
        if type_id not in listed_target_ids or qty <= 0:
            continue
        if sell_qty.get(type_id, 0.0) <= 0:
            sde_type = storage.get_sde_type(type_id)
            name = sde_type[2] if sde_type else str(type_id)
            rows.append(UnlistedStockRow(type_id=type_id, type_name=name, stock_quantity=qty))
    rows.sort(key=lambda r: r.stock_quantity, reverse=True)
    return {"rows": rows}


def do_list_current_jobs() -> dict:
    """Every active character + corp industry job, for the Industriejobs tab."""
    return {"rows": jobs.list_current_jobs()}


def do_character_slot_overview() -> dict:
    """Total/used/free industry job slots per registered character, for the
    Charakter-Slots tab."""
    return {"rows": jobs.character_slot_overview()}


def do_list_owned_blueprints() -> dict:
    """Every owned blueprint (character + corp), aggregated across identical
    (type_id, is_original, ME, TE, runs) groups, for the Blueprints tab -
    surfaces storage.load_owned_blueprints() (synced but never shown before).
    A BPO is identified by runs == -1 (ESI's convention for "original"), not
    by any explicit is_blueprint_copy flag (that field only exists on the
    *asset* tables, not the blueprint tables - see esi_sync.py's
    _blueprint_rows)."""
    grouped: dict[tuple, int] = {}
    for type_id, quantity, material_efficiency, time_efficiency, runs in storage.load_owned_blueprints():
        is_original = runs == -1
        key = (type_id, is_original, material_efficiency, time_efficiency, None if is_original else runs)
        # ESI's `quantity` field for a blueprint item is usually a sentinel
        # (-1 original / -2 copy), not an actual stack size - only add it
        # when it looks like a real positive count, else this row is 1 item.
        grouped[key] = grouped.get(key, 0) + (quantity if quantity and quantity > 0 else 1)

    rows = []
    for (type_id, is_original, me, te, runs), qty in grouped.items():
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        rows.append(OwnedBlueprintRow(
            type_id=type_id, type_name=name, is_original=is_original,
            quantity=qty, material_efficiency=me, time_efficiency=te, runs=runs,
        ))
    rows.sort(key=lambda r: r.type_name)
    return {"rows": rows}

"""Pipeline actions for the Module Reprocessing Import tool - see CLAUDE.md's
"Architecture" section: cli.py/the FastAPI router call these do_* functions,
never storage.py/engine.py directly.

Discovery (do_discover_candidates) runs synchronously in the request, not
via pipeline_runner - same shape as station_trading/candidate_discovery.py's
own discover_candidates (a single Goonmetrics current-price bulk dump,
already tolerated there as a plain blocking call for an even larger "the
whole Jita market" universe), not Trading's own multi-day chunked-history
Search+Add+Clean Up (a pipeline_runner job, since that one is minutes-long,
not one ~30s HTTP call - see goonmetrics_client.py's current_prices
docstring for the measured figure this is based on).
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

import requests

from .. import storage
from ..actions import ActionError, list_shared_trading_characters, structure_book_auth_roles
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, TRADING_CONFIG, ConfigError, OAuthConfig, TradingConfig, save_tenant_config_overrides
from ..esi_client import ESIClient, ESIError
from ..goonmetrics_client import GoonmetricsClient
from .candidate_discovery import build_module_candidate_universe
from .config import MODULE_REPROCESSING_CONFIG, ModuleReprocessingConfig
from .models import DiscoveredModuleResult, ModuleCandidate, ModuleShortlistRow
from .pricing import estimate_discovered_candidates, evaluate_module_shortlist, mineral_type_ids_for

log = logging.getLogger("eve_trader.module_reprocessing.actions")

JITA_MARKET = "jita"


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def _seller_roles(tm: TokenManager) -> list[str]:
    """Same reasoning as refining/actions.py's own _seller_roles - reuses
    whichever of Trading's shared seller characters actually has docking
    access to C-J for the structure order book (mineral sell side); no
    Module-Reprocessing-specific login (this tool has no OAuth role
    namespace of its own, same as Ore & Minerals - see auth.py's
    TOOL_ROLE_PREFIXES)."""
    return structure_book_auth_roles(list_shared_trading_characters(tm))


# ------------------------------------------------------------- Discovery
# In-process, per-tenant cache of the last Discover run's results - never
# persisted (see do_discover_candidates's own docstring for why: this is a
# triage aid over a large universe, not a decision anyone should act on
# without a live-priced Refresh first). Same "module-level dict + lock"
# shape as production/engine.py's own discover cache, minus the TTL - there
# is no implicit re-fetch-on-read here, only an explicit Discover run
# overwrites this tenant's entry.
_discover_lock = threading.Lock()
_discover_results: dict[str, list[dict]] = {}


def _discovered_result_to_dict(r: DiscoveredModuleResult) -> dict:
    return {
        "type_id": r.type_id, "item": r.item, "volume_m3": r.volume_m3,
        "est_landed_cost": r.est_landed_cost, "est_mineral_value": r.est_mineral_value,
        "est_profit_per_unit": r.est_profit_per_unit, "est_margin": r.est_margin,
    }


def do_discover_candidates(cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG,
                            trading_cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Scans the full T1/Meta module+drone SDE universe (build_module_
    candidate_universe) and estimates each one's reprocessing economics from
    a Goonmetrics current-price bulk dump - one HTTP call per market,
    regardless of how large the universe is (see module_reprocessing/
    pricing.py's own docstring for why this, not live ESI, is the right
    source for a triage-only pass). Results replace this tenant's entry in
    the in-process cache below for do_get_discovered_candidates to read;
    nothing is written to Postgres here - the user picks which rows are
    worth tracking live via do_add_to_shortlist.

    Warms get_sde_type/get_type_materials' lru_caches in two bulk calls up
    front (storage.get_sde_types_bulk/get_type_materials_bulk) so the
    per-candidate reprocessing math below never opens its own DB connection
    - with those warmed, the only real cost left is the Goonmetrics
    dump(s), same as station_trading's own discover_candidates."""
    candidates = build_module_candidate_universe()
    if not candidates:
        raise ActionError("No T1/Meta module or drone types found in the SDE cache - run Refresh SDE first.")

    type_ids = [c.type_id for c in candidates]
    storage.get_sde_types_bulk(type_ids)
    storage.get_type_materials_bulk(type_ids)

    client = GoonmetricsClient(trading_cfg)
    try:
        jita_prices_by_id = {p.type_id: p for p in client.current_prices(JITA_MARKET)}
    except requests.RequestException as e:
        raise ActionError(f"Could not fetch Jita prices from Goonmetrics ({e}).") from e

    mineral_ids = set(mineral_type_ids_for(candidates))
    mineral_prices_by_id = {}
    try:
        # Goonmetrics' own current-price dump is cached module-wide for a
        # while (see GoonmetricsClient.current_prices) - a Jita dump just
        # fetched above and a C-J dump here are two independent cache
        # entries, but either can already be warm from another feature
        # (Production's home_prices, the Mineral Shopping List) having
        # fetched the same market recently.
        mineral_prices_by_id = {
            p.type_id: p for p in client.current_prices(trading_cfg.structure_market_slug) if p.type_id in mineral_ids
        }
    except requests.RequestException as e:
        log.warning("Goonmetrics home-market fetch failed (%s) - mineral values will be missing this Discover run.", e)

    results = estimate_discovered_candidates(candidates, jita_prices_by_id, mineral_prices_by_id, trading_cfg, cfg)
    results.sort(key=lambda r: (r.est_profit_per_unit is None, -(r.est_profit_per_unit or 0.0)))

    tenant_id = storage.get_current_tenant()
    rows = [_discovered_result_to_dict(r) for r in results]
    with _discover_lock:
        _discover_results[tenant_id] = rows

    profitable = sum(1 for r in results if r.est_profit_per_unit is not None and r.est_profit_per_unit > 0)
    return {"scanned": len(results), "estimated_profitable": profitable}


def do_get_discovered_candidates() -> list[dict]:
    """The last do_discover_candidates run's results for this tenant - empty
    until Discover has been run at least once this process's lifetime (an
    in-process cache, not persisted - see do_discover_candidates)."""
    tenant_id = storage.get_current_tenant()
    with _discover_lock:
        return list(_discover_results.get(tenant_id, []))


# ------------------------------------------------------------- Shortlist
def do_add_to_shortlist(item_ids: list[int]) -> dict:
    """Adds the given type_ids (picked from a Discover run's results - see
    do_get_discovered_candidates) to the real, live-ESI-priced shortlist.
    Unlike Ore & Minerals' own do_add_ore_to_shortlist (adds every candidate
    automatically - a fine default for a ~80-item universe), this tool's
    candidate universe is too large to auto-track in full, so the user picks
    which discovered rows are worth live-pricing on every Refresh (confirmed
    with the user during planning). Re-resolves each type_id's name from the
    SDE cache rather than trusting a client-sent label, same as refining/
    actions.py's do_save_mineral_requirements."""
    if not item_ids:
        raise ActionError("No items selected.")
    rows = []
    for type_id in item_ids:
        sde_row = storage.get_sde_type(type_id)
        if not sde_row:
            raise ActionError(f"Type {type_id} isn't in the SDE cache - run Refresh SDE first.")
        rows.append((type_id, sde_row[2], True))
    storage.upsert_module_reprocessing_shortlist(rows)
    return {"added": len(rows)}


def do_refresh_shortlist(cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG,
                          trading_cfg: TradingConfig = TRADING_CONFIG,
                          oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Re-fetches live market data for every shortlisted item and recomputes
    each one's profit/decision, then saves a new snapshot - mirrors
    refining/actions.py's do_refresh_ore_shortlist. The module's own
    purchase side is priced from cfg.purchase_region_id's public regional
    order book (Default Jita, no auth needed); the mineral sell side reuses
    Trading's own seller character for C-J's structure order book (with a
    Goonmetrics fallback), same as Ore & Minerals."""
    shortlist = storage.load_module_reprocessing_shortlist()
    if not shortlist:
        raise ActionError("Shortlist is empty - add candidates from Discover first.")
    active_by_id = {item_id: active for item_id, _item, active in shortlist}
    item_ids = list(active_by_id.keys())

    sde_rows = storage.get_sde_types_bulk(item_ids)
    candidates = [
        ModuleCandidate(type_id=item_id, item=item_name,
                         volume_m3=(sde_rows.get(item_id)[3] if sde_rows.get(item_id) else 0.0) or 0.0)
        for item_id, item_name, _active in shortlist
    ]

    tm = TokenManager(oauth_cfg)
    seller_roles = _seller_roles(tm)
    client = ESIClient(trading_cfg, tm)

    try:
        item_stats_by_id = client.region_order_stats_bulk(cfg.purchase_region_id, item_ids)
    except (ESIError, requests.RequestException) as e:
        raise ActionError(f"Could not fetch the purchase region's order book ({e}).") from e

    mineral_ids = mineral_type_ids_for(candidates)
    try:
        # Falls back to a Goonmetrics current-price snapshot when no seller
        # is logged in or the real call fails - see
        # structure_order_stats_bulk_or_goonmetrics's own docstring.
        mineral_stats_by_id, priced_via_fallback = client.structure_order_stats_bulk_or_goonmetrics(
            trading_cfg.structure_id, mineral_ids, auth_roles=seller_roles,
            goonmetrics_market_slug=trading_cfg.structure_market_slug)
    except ESIError as e:
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                           f"Does the seller character still have docking access?") from e

    rows = evaluate_module_shortlist(candidates, active_by_id, item_stats_by_id, mineral_stats_by_id, trading_cfg, cfg)
    run_ts = now_ts()
    storage.save_module_reprocessing_shortlist_snapshot([_row_to_tuple(r) for r in rows], run_ts)
    storage.set_esi_sync_time("module_reprocessing", run_ts)

    import_count = sum(1 for r in rows if r.decision == "Import")
    return {"evaluated": len(rows), "import_candidates": import_count, "priced_via_fallback": priced_via_fallback}


def _row_to_tuple(r: ModuleShortlistRow) -> tuple:
    return (r.item_id, r.item, r.active, r.volume_m3, r.landed_cost, r.yield_pct, r.mineral_value,
            r.refining_tax, r.net_sell, r.sell_listed_qty, r.profit_per_unit, r.margin, r.profit_per_m3, r.decision)


def do_deactivate_shortlist_items(item_ids: list[int]) -> dict:
    storage.deactivate_module_reprocessing_shortlist_items(item_ids)
    return {"deactivated": len(item_ids)}


def do_activate_shortlist_items(item_ids: list[int]) -> dict:
    storage.activate_module_reprocessing_shortlist_items(item_ids)
    return {"activated": len(item_ids)}


def do_update_settings(updates: dict, cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG) -> dict:
    """Persists `updates` to tenant_settings and applies them to the live
    MODULE_REPROCESSING_CONFIG immediately (see Module Reprocessing Settings
    tab). No enum-style fields exist on ModuleReprocessingConfig (no
    structure_type/rig_tier/implant - see that class's own docstring), so
    the generic type/range check inside save_tenant_config_overrides is
    enough; unlike refining/actions.py's do_update_settings, there is no
    extra validate_module_reprocessing_overrides step to call first."""
    try:
        save_tenant_config_overrides("module_reprocessing", updates, cfg, cfg_type=ModuleReprocessingConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return updates

"""Pipeline actions for the Module Reprocessing Import tool - see CLAUDE.md's
"Architecture" section: cli.py/the FastAPI router call these do_* functions,
never storage.py/engine.py directly.

do_refresh_shortlist runs synchronously in the request, not via
pipeline_runner - same shape as station_trading/actions.py's own
do_refresh_shortlist (a single Goonmetrics current-price bulk dump, already
tolerated there as a plain blocking call for an even larger "the whole Jita
market" universe), not Trading's own multi-day chunked-history Search+Add+
Clean Up (a pipeline_runner job, since that one is minutes-long, not one
~30s HTTP call - see goonmetrics_client.py's current_prices docstring for
the measured figure this is based on).

Discovery/add-to-shortlist is NOT a separate manual step here (confirmed
with the user 2026-09-27, after building an earlier manual-pick version
first: reviewing and picking candidates one by one doesn't scale over this
tool's large T1/Meta module+drone universe). do_refresh_shortlist auto-
discovers and auto-adds every candidate clearing cfg.min_profit_threshold/
min_margin_threshold on every run, mirroring station_trading/actions.py's
own do_refresh_shortlist exactly - the real noise filter is that threshold,
not a manual review step (see module_reprocessing/config.py's own
enforce_shortlist_cap/max_active_shortlist_items docstring for the opt-in
safety valve on top of it).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import requests

from .. import storage
from ..actions import ActionError, list_shared_trading_characters, structure_book_auth_roles
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, TRADING_CONFIG, ConfigError, OAuthConfig, TradingConfig, save_tenant_config_overrides
from ..esi_client import ESIClient, ESIError
from ..goonmetrics_client import GoonmetricsClient
from ..refining import pricing as refining_pricing
from ..refining.candidate_discovery import build_ore_candidate_universe
from ..refining.config import REFINING_CONFIG, RefiningConfig
from ..refining.engine import apply_reprocessing_yield, ore_ice_yield
from . import pricing as module_pricing
from .candidate_discovery import discover_candidates
from .config import MODULE_REPROCESSING_CONFIG, ModuleReprocessingConfig
from .engine import scrapmetal_yield
from .models import (
    MODULE_CATEGORY, ORE_CATEGORY, MineralOption, MineralRequirement, ModuleCandidate, ModuleShoppingListPlan,
    ModuleShortlistRow, ReprocessOption,
)
from .pricing import evaluate_module_shortlist, mineral_type_ids_for
from .shopping_optimizer import OptimizationError, optimize_shopping_list

log = logging.getLogger("eve_trader.module_reprocessing.actions")


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


def do_refresh_shortlist(cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG,
                          trading_cfg: TradingConfig = TRADING_CONFIG,
                          oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """One button, does everything (mirrors station_trading/actions.py's own
    do_refresh_shortlist):
      1. Auto-discover: a cheap Goonmetrics-only scan of the whole candidate
         universe (candidate_discovery.discover_candidates), auto-upserted
         into the persisted shortlist - a re-discovered item's `active` flag
         is left untouched (see storage.upsert_module_reprocessing_shortlist),
         so a manually deactivated item never silently comes back just
         because it's still economically plausible.
      2. Live-price every item now on the shortlist via real ESI order-book
         stats and save a new snapshot - the module's own purchase side from
         cfg.purchase_region_id's public regional order book (Default Jita,
         no auth needed), the mineral sell side from Trading's own seller
         character at C-J's structure order book (with a Goonmetrics
         fallback), same as Ore & Minerals."""
    try:
        discovered = discover_candidates(cfg, trading_cfg)
    except requests.RequestException as e:
        raise ActionError(f"Could not fetch Jita prices from Goonmetrics ({e}).") from e
    if discovered:
        storage.upsert_module_reprocessing_shortlist([(d.type_id, d.item) for d in discovered])

    shortlist = storage.load_module_reprocessing_shortlist()
    if not shortlist:
        raise ActionError("No candidates clear the configured margin/profit threshold yet - "
                           "lower it in Settings (or turn on 'Ignore margin/profit thresholds "
                           "entirely'), or check back once Goonmetrics has fresher data.")
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
    return {"discovered": len(discovered), "evaluated": len(rows), "import_candidates": import_count,
            "priced_via_fallback": priced_via_fallback}


def _row_to_tuple(r: ModuleShortlistRow) -> tuple:
    return (r.item_id, r.item, r.active, r.volume_m3, r.landed_cost, r.yield_pct, r.mineral_value,
            r.refining_tax, r.net_sell, r.sell_listed_qty, r.profit_per_unit, r.margin, r.profit_per_m3, r.decision)


def do_deactivate_shortlist_items(item_ids: list[int]) -> dict:
    """Manual override - a deactivated item is never re-discovered back onto
    the shortlist automatically (see storage.upsert_module_reprocessing_
    shortlist's own docstring); do_activate_shortlist_items below undoes it."""
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


# --------------------------------------------------- Mineral Shopping List
# The same feature as Ore & Minerals' own Mineral Shopping List (GitHub issue
# #93, refining/actions.py's do_*_mineral_* functions), but the optimizer
# (shopping_optimizer.py) considers compressed ore/ice AND this tool's own
# T1/Meta modules/drones as reprocessing sources in one combined plan.
#
# The two source kinds are deliberately scoped differently - do not "fix"
# this into a symmetry:
#   - Ore/ice: the FULL SDE-derived universe (refining.candidate_discovery.
#     build_ore_candidate_universe, ~80 types), exactly like Ore & Minerals'
#     own shopping list - small enough to live-price in one bulk ESI call per
#     Optimize click.
#   - Modules/drones: only this tool's own persisted, ACTIVE shortlist
#     (storage.load_module_reprocessing_shortlist), never the raw module
#     universe (build_module_candidate_universe - thousands of types). ESI
#     has no multi-type regional order-book endpoint (one call per type_id),
#     so live-pricing the whole universe on every Optimize click would be
#     impractically slow and rate-limit-risky; the shortlist is the bounded,
#     already-curated set Refresh Shortlist live-prices the same way. An
#     item deactivated there is the user's own "don't buy this" and stays
#     out of the plan too.
def _module_shopping_candidates() -> list[ModuleCandidate]:
    """The ACTIVE module shortlist as ModuleCandidates (see the section comment
    above for why shortlist-scoped). Warms get_sde_type/get_type_materials'
    caches in two bulk calls so per-item yield math opens no extra DB
    connections - same as candidate_discovery.discover_candidates."""
    shortlist = [(item_id, item) for item_id, item, active in storage.load_module_reprocessing_shortlist() if active]
    if not shortlist:
        return []
    item_ids = [item_id for item_id, _item in shortlist]
    sde_rows = storage.get_sde_types_bulk(item_ids)
    storage.get_type_materials_bulk(item_ids)
    return [
        ModuleCandidate(type_id=item_id, item=item,
                         volume_m3=(sde_rows.get(item_id)[3] if sde_rows.get(item_id) else 0.0) or 0.0)
        for item_id, item in shortlist
    ]


def do_list_shoppable_minerals() -> list[dict]:
    """Every distinct mineral the ore/ice universe OR the active module
    shortlist can reprocess into, name-resolved - what the Shopping List's
    "add a mineral" picker offers. Mirrors refining/actions.py's
    do_list_refinable_minerals (real SDE material rows, not a hardcoded list)."""
    ids = set(refining_pricing.mineral_type_ids_for(build_ore_candidate_universe()))
    ids.update(module_pricing.mineral_type_ids_for(_module_shopping_candidates()))
    minerals = []
    for type_id in sorted(ids):
        row = storage.get_sde_type(type_id)
        if row:
            minerals.append({"type_id": type_id, "name": row[2]})
    minerals.sort(key=lambda m: m["name"])
    return minerals


def do_load_module_shopping_requirements() -> list[dict]:
    return [{"type_id": type_id, "name": name, "required_qty": qty}
            for type_id, name, qty in storage.load_module_shopping_requirements()]


def do_save_module_shopping_requirements(requirements: list[dict]) -> dict:
    """Replaces the whole saved requirement list (see storage.
    replace_module_shopping_requirements). Same validation as refining/
    actions.py's do_save_mineral_requirements: numeric type_id, positive
    required_qty, no duplicates, type must exist in the SDE cache, and the
    name is always re-resolved from the SDE rather than trusted from the
    caller."""
    rows = []
    seen: set[int] = set()
    for entry in requirements:
        try:
            type_id = int(entry["type_id"])
            qty = float(entry["required_qty"])
        except (KeyError, TypeError, ValueError) as e:
            raise ActionError(f"Each requirement needs a numeric type_id and required_qty ({entry!r}).") from e
        if qty <= 0:
            raise ActionError(f"Required quantity for type {type_id} must be greater than 0.")
        if type_id in seen:
            raise ActionError(f"Type {type_id} is listed twice - each mineral can only have one required quantity.")
        sde_row = storage.get_sde_type(type_id)
        if not sde_row:
            raise ActionError(f"Type {type_id} isn't in the SDE cache - run Refresh SDE first.")
        seen.add(type_id)
        rows.append((type_id, sde_row[2], qty))
    storage.replace_module_shopping_requirements(rows)
    return {"saved": len(rows)}


def _ore_reprocess_option(candidate, jita_stats, refining_cfg: RefiningConfig,
                           trading_cfg: TradingConfig) -> Optional[ReprocessOption]:
    """One compressed ore/ice LP column - the same pricing/yield as refining/
    actions.py's _ore_option (refining.pricing.landed_cost_per_unit,
    refining.engine.ore_ice_yield net of RefiningConfig.refining_tax_rate,
    apply_reprocessing_yield), so an ore costs and yields exactly what it
    does in Ore & Minerals' own shopping list. None when unusable (not
    listed, no portion size, nothing to reprocess into)."""
    portion_size = storage.get_portion_size(candidate.type_id)
    jita_sell = jita_stats.sell_percentile if jita_stats else None
    unit_cost = refining_pricing.landed_cost_per_unit(jita_sell, candidate.volume_m3, trading_cfg)
    if unit_cost is None or not portion_size:
        return None
    # Tax as reduced yield - see shopping_optimizer.py's decision 2.
    effective_yield = ore_ice_yield(refining_cfg, candidate.family) * (1 - refining_cfg.refining_tax_rate)
    yield_per_portion = apply_reprocessing_yield(candidate.type_id, portion_size, effective_yield)
    if not yield_per_portion:
        return None
    return ReprocessOption(type_id=candidate.type_id, item=candidate.item, category=ORE_CATEGORY,
                            family=candidate.family, is_ice=candidate.is_ice, volume_m3=candidate.volume_m3,
                            portion_size=portion_size, landed_cost_per_unit=unit_cost,
                            yield_per_portion=yield_per_portion)


def _module_reprocess_option(candidate: ModuleCandidate, item_stats, cfg: ModuleReprocessingConfig,
                              trading_cfg: TradingConfig) -> Optional[ReprocessOption]:
    """One module/drone LP column - this tool's own pricing (module_
    reprocessing.pricing.landed_cost_per_unit, i.e. cfg.freight_cost_per_m3)
    and scrapmetal yield net of cfg.refining_tax_rate, same shape as
    pricing.evaluate_module_item. None when unusable."""
    portion_size = storage.get_portion_size(candidate.type_id)
    buy_price = item_stats.sell_percentile if item_stats else None
    unit_cost = module_pricing.landed_cost_per_unit(buy_price, candidate.volume_m3, trading_cfg, cfg)
    if unit_cost is None or not portion_size:
        return None
    # Tax as reduced yield - see shopping_optimizer.py's decision 2.
    effective_yield = scrapmetal_yield(cfg) * (1 - cfg.refining_tax_rate)
    yield_per_portion = apply_reprocessing_yield(candidate.type_id, portion_size, effective_yield)
    if not yield_per_portion:
        return None
    return ReprocessOption(type_id=candidate.type_id, item=candidate.item, category=MODULE_CATEGORY,
                            family=None, is_ice=False, volume_m3=candidate.volume_m3,
                            portion_size=portion_size, landed_cost_per_unit=unit_cost,
                            yield_per_portion=yield_per_portion)


def _home_mineral_prices(mineral_ids: set[int], trading_cfg: TradingConfig) -> dict:
    """Best-effort C-J home-market quotes for the required minerals, keyed by
    type_id. Prefers trading_cfg.structure_id (GoonmetricsClient.
    station_current_prices) and falls back to trading_cfg.structure_market_
    slug (current_prices) only when structure_id isn't set - the same
    preference order and reasoning as candidate_discovery.discover_candidates.
    A Goonmetrics outage only means Jita-only mineral pricing, never a hard
    failure."""
    if not mineral_ids:
        return {}
    if trading_cfg.structure_id:
        try:
            return GoonmetricsClient(trading_cfg).station_current_prices(trading_cfg.structure_id, mineral_ids)
        except requests.RequestException as e:
            log.warning("Goonmetrics home-market fetch failed (station_id, %s) - "
                        "falling back to Jita-only mineral pricing.", e)
    elif trading_cfg.structure_market_slug:
        try:
            return {p.type_id: p for p in GoonmetricsClient(trading_cfg).current_prices(trading_cfg.structure_market_slug)
                    if p.type_id in mineral_ids}
        except requests.RequestException as e:
            log.warning("Goonmetrics home-market fetch failed (market slug, %s) - "
                        "falling back to Jita-only mineral pricing.", e)
    return {}


def do_optimize_module_shopping_list(requirements: Optional[list[dict]] = None,
                                      cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG,
                                      trading_cfg: TradingConfig = TRADING_CONFIG,
                                      refining_cfg: RefiningConfig = REFINING_CONFIG,
                                      oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Solves "cheapest way to acquire these minerals" across every compressed
    ore/ice type AND every active module-shortlist item at once (see
    shopping_optimizer.py). `requirements` defaults to the saved list;
    passing one solves an ad-hoc list without persisting it.

    Like Ore & Minerals' own do_optimize_mineral_shopping_list this needs NO
    logged-in character: every price is a *buy* price from a public regional
    order book (ore and minerals from cfg.input_hub_region_id, modules
    from cfg.purchase_region_id - the same source do_refresh_shortlist
    buys from) or an unauthenticated Goonmetrics home-market quote.

    Each source kind keeps its own tool's economics: ore columns are priced
    and yielded exactly as Ore & Minerals prices them (TradingConfig haul
    cost, RefiningConfig yield/tax), module columns exactly as this tool's
    own Shortlist prices them (ModuleReprocessingConfig freight/scrapmetal
    yield/tax). A required mineral's direct-buy alternative is the cheaper
    of Jita-landed (refining.pricing.landed_cost_per_unit, same as Ore &
    Minerals) and C-J's home market (see _home_mineral_prices)."""
    entries = requirements if requirements is not None else do_load_module_shopping_requirements()
    wanted: list[MineralRequirement] = []
    for entry in entries:
        type_id, qty = int(entry["type_id"]), float(entry["required_qty"])
        if qty <= 0:
            continue
        sde_row = storage.get_sde_type(type_id)
        wanted.append(MineralRequirement(type_id=type_id,
                                          name=entry.get("name") or (sde_row[2] if sde_row else str(type_id)),
                                          required_qty=qty))
    if not wanted:
        raise ActionError("No mineral requirements yet - add at least one mineral and quantity first.")

    ore_candidates = build_ore_candidate_universe()
    module_candidates = _module_shopping_candidates()
    if not ore_candidates and not module_candidates:
        raise ActionError("No compressed ore/ice types found in the SDE cache and no active modules on the "
                           "shortlist - run Refresh SDE (and Refresh Shortlist) first.")

    client = ESIClient(trading_cfg, TokenManager(oauth_cfg))
    ore_ids = [c.type_id for c in ore_candidates]
    module_ids = [c.type_id for c in module_candidates]
    mineral_ids = [r.type_id for r in wanted]
    jita_ids = set(ore_ids + mineral_ids)
    try:
        if cfg.purchase_region_id == cfg.input_hub_region_id:
            # The common case (both default to Jita) - one bulk call, not two.
            stats_by_id = client.region_order_stats_bulk(cfg.input_hub_region_id, sorted(jita_ids | set(module_ids)))
            module_stats_by_id = stats_by_id
        else:
            stats_by_id = client.region_order_stats_bulk(cfg.input_hub_region_id, sorted(jita_ids))
            module_stats_by_id = (client.region_order_stats_bulk(cfg.purchase_region_id, sorted(set(module_ids)))
                                  if module_ids else {})
    except (ESIError, requests.RequestException) as e:
        # Transport-level failure - see refining/actions.py's identical wrap.
        raise ActionError(f"Could not fetch the order book ({e}).") from e

    options = [o for o in (_ore_reprocess_option(c, stats_by_id.get(c.type_id), refining_cfg, trading_cfg)
                           for c in ore_candidates) if o is not None]
    options += [o for o in (_module_reprocess_option(c, module_stats_by_id.get(c.type_id), cfg, trading_cfg)
                            for c in module_candidates) if o is not None]

    home_quotes = _home_mineral_prices(set(mineral_ids), trading_cfg)

    mineral_options = {}
    for req in wanted:
        sde_row = storage.get_sde_type(req.type_id)
        volume = sde_row[3] if sde_row and sde_row[3] else 0.0
        stats = stats_by_id.get(req.type_id)
        jita_cost = refining_pricing.landed_cost_per_unit(stats.sell_percentile if stats else None, volume, trading_cfg)

        home_quote = home_quotes.get(req.type_id)
        home_cost = (home_quote.sell * (1 + trading_cfg.jita_buy_broker_fee)
                     if home_quote and home_quote.sell and home_quote.sell > 0 else None)

        if home_cost is not None and (jita_cost is None or home_cost < jita_cost):
            unit_cost, source = home_cost, "Home"
        elif jita_cost is not None:
            unit_cost, source = jita_cost, "Jita"
        else:
            unit_cost, source = None, None

        mineral_options[req.type_id] = MineralOption(
            type_id=req.type_id, name=req.name, landed_cost_per_unit=unit_cost, source=source,
        )

    try:
        plan = optimize_shopping_list(wanted, options, mineral_options)
    except OptimizationError as e:
        raise ActionError(str(e)) from e
    return _shopping_plan_to_dict(plan)


def _shopping_plan_to_dict(plan: ModuleShoppingListPlan) -> dict:
    return {
        "reprocess_purchases": [vars(p) for p in plan.reprocess_purchases],
        "direct_purchases": [vars(p) for p in plan.direct_purchases],
        "coverage": [vars(c) for c in plan.coverage],
        "reprocess_cost": plan.reprocess_cost, "direct_cost": plan.direct_cost, "total_cost": plan.total_cost,
        "lp_cost": plan.lp_cost, "all_direct_cost": plan.all_direct_cost,
        "savings_vs_all_direct": plan.savings_vs_all_direct, "total_volume_m3": plan.total_volume_m3,
    }

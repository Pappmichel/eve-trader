"""Pipeline actions for the Ore Shortlist (GitHub issue #91) - see CLAUDE.md's
"Architecture" section: `cli.py`/the FastAPI router call these do_* functions,
never storage.py/engine.py directly.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import requests

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, TRADING_CONFIG, ConfigError, OAuthConfig, TradingConfig, save_tenant_config_overrides
from ..esi_client import ESIClient, ESIError
from ..goonmetrics_client import GoonmetricsClient
from ..production.config import PRODUCTION_CONFIG, ProductionConfig
from ..production.pricing import home_prices
from .candidate_discovery import build_ore_candidate_universe
from .config import REFINING_CONFIG, RefiningConfig, validate_refining_overrides
from .engine import apply_reprocessing_yield, ore_ice_yield
from .models import MineralOption, MineralRequirement, OreOption, OreShortlistRow, ShoppingListPlan
from .optimizer import OptimizationError, optimize_shopping_list
from .paste_parser import merge_duplicate_stacks, parse_paste
from .pricing import evaluate_ore_shortlist, landed_cost_per_unit, mineral_type_ids_for
from .reprocessing import (
    REPROCESS_DECISION, ReprocessingQuoteRow, evaluate_reprocessing_line, mineral_type_ids_for_lines,
    resolve_type_id,
)

log = logging.getLogger("eve_trader.refining.actions")


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def _seller_role(tm: TokenManager) -> Optional[str]:
    """Reuses Trading's own seller role/token - no Ore-specific login (see
    module docstring). Any one registered seller with docking access is
    enough (GitHub issue #46's own multi-character precedent) - falls back
    to the legacy fixed "seller" key for a not-yet-re-logged-in setup, same
    fallback doctrine/engine.py's own seller-role lookup uses."""
    return next(iter(tm.list_roles("seller")), None) or ("seller" if tm.has_token("seller") else None)


def do_add_ore_to_shortlist() -> dict:
    """Adds every candidate from the fixed SDE-derived universe
    (build_ore_candidate_universe) not already on the shortlist - unlike
    Trading's own add step, there's no backtest/recommendation gate first
    (confirmed with the user: the set of compressed ore/ice types is small
    and stable, every candidate is worth tracking)."""
    candidates = build_ore_candidate_universe()
    if not candidates:
        raise ActionError("No compressed ore/ice types found in the SDE cache - run Refresh SDE first.")
    existing_ids = {item_id for item_id, *_ in storage.load_ore_shortlist()}
    new_rows = [(c.type_id, c.item, c.family, c.is_ice, True) for c in candidates if c.type_id not in existing_ids]
    if new_rows:
        storage.upsert_ore_shortlist(new_rows)
    return {"added": len(new_rows), "already_tracked": len(candidates) - len(new_rows)}


def do_refresh_ore_shortlist(trading_cfg: TradingConfig = TRADING_CONFIG,
                              refining_cfg: RefiningConfig = REFINING_CONFIG,
                              oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Re-fetches live market data for every shortlist item and recomputes
    each one's profit/decision, then saves a new snapshot. Reuses Trading's
    own seller character (same C-J structure both tools sell at - confirmed
    with the user during planning rather than a separate Ore-specific login)
    for the structure order book; Jita's regional order book needs no auth."""
    candidates = build_ore_candidate_universe()
    shortlist = storage.load_ore_shortlist()
    if not shortlist:
        raise ActionError("Ore Shortlist is empty - click 'Add Candidates' first.")
    active_by_id = {item_id: active for item_id, _item, _family, _is_ice, active in shortlist}
    tracked_ids = set(active_by_id)
    tracked_candidates = [c for c in candidates if c.type_id in tracked_ids]

    tm = TokenManager(oauth_cfg)
    seller_role = _seller_role(tm)
    if seller_role is None:
        raise ActionError("Seller character isn't logged in yet (Trading -> Login -> Seller).")
    client = ESIClient(trading_cfg, tm)

    ore_type_ids = [c.type_id for c in tracked_candidates]
    jita_stats_by_id = client.region_order_stats_bulk(trading_cfg.jita_region_id, ore_type_ids)

    mineral_ids = mineral_type_ids_for(tracked_candidates)
    try:
        mineral_stats_by_id = client.structure_order_stats_bulk(
            trading_cfg.structure_id, mineral_ids, auth_role=seller_role)
    except ESIError as e:
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                           f"Does the seller character still have docking access?") from e

    rows = evaluate_ore_shortlist(tracked_candidates, active_by_id, jita_stats_by_id, mineral_stats_by_id,
                                   trading_cfg, refining_cfg)
    run_ts = now_ts()
    storage.save_ore_shortlist_snapshot([_row_to_tuple(r) for r in rows], run_ts)
    storage.set_esi_sync_time("refining", run_ts)

    import_count = sum(1 for r in rows if r.decision == "Import")
    return {"evaluated": len(rows), "import_candidates": import_count}


def _row_to_tuple(r: OreShortlistRow) -> tuple:
    return (r.item_id, r.item, r.family, r.is_ice, r.active, r.volume_m3, r.landed_cost, r.yield_pct,
            r.mineral_value, r.refining_tax, r.net_sell, r.sell_listed_qty, r.profit_per_unit, r.margin,
            r.profit_per_m3, r.decision)


def do_deactivate_ore_shortlist_items(item_ids: list[int]) -> dict:
    storage.deactivate_ore_shortlist_items(item_ids)
    return {"deactivated": len(item_ids)}


def do_activate_ore_shortlist_items(item_ids: list[int]) -> dict:
    storage.activate_ore_shortlist_items(item_ids)
    return {"activated": len(item_ids)}


def do_quote_reprocessing(paste_text: str, trading_cfg: TradingConfig = TRADING_CONFIG,
                           refining_cfg: RefiningConfig = REFINING_CONFIG,
                           oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """GitHub issue #92: parses an EVE inventory "Copy As" paste, quotes each
    item (sell-as-is vs. scrapmetal-reprocessed), and returns both the
    per-item rows and a totals summary. Reuses Trading's own seller
    character/C-J structure, same as do_refresh_ore_shortlist - both the
    item's own sell price and its mineral yield's sell price are C-J-only
    (consistent with #91 and Production's established rule)."""
    if not paste_text or not paste_text.strip():
        raise ActionError("Paste is empty - copy items from an Inventory window's list view first.")

    all_lines = parse_paste(paste_text)
    error_lines = [line for line in all_lines if line.error]
    parsed = merge_duplicate_stacks(all_lines)
    if not parsed and not error_lines:
        raise ActionError("Could not parse any items from the paste.")

    tm = TokenManager(oauth_cfg)
    seller_role = _seller_role(tm)
    if seller_role is None:
        raise ActionError("Seller character isn't logged in yet (Trading -> Login -> Seller).")
    client = ESIClient(trading_cfg, tm)

    type_ids = [tid for tid in (resolve_type_id(line.name) for line in parsed) if tid is not None]
    mineral_ids = mineral_type_ids_for_lines(type_ids)
    all_ids = sorted(set(type_ids) | set(mineral_ids))
    try:
        stats_by_id = client.structure_order_stats_bulk(trading_cfg.structure_id, all_ids, auth_role=seller_role)
    except ESIError as e:
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                           f"Does the seller character still have docking access?") from e

    rows = [error_line_to_row(line) for line in error_lines]
    for line in parsed:
        type_id = resolve_type_id(line.name)
        item_stats = stats_by_id.get(type_id) if type_id is not None else None
        rows.append(evaluate_reprocessing_line(line, item_stats, stats_by_id, trading_cfg, refining_cfg))

    reprocess_rows = [r for r in rows if r.decision == REPROCESS_DECISION]
    totals = {
        "reprocess_count": len(reprocess_rows),
        "total_mineral_value": sum(r.mineral_value or 0.0 for r in reprocess_rows),
        "total_refined_value": sum(r.refined_value or 0.0 for r in reprocess_rows),
        "total_sell_as_is_value": sum(r.sell_as_is_value or 0.0 for r in rows if r.sell_as_is_value is not None),
    }
    return {"rows": [_reprocessing_row_to_dict(r) for r in rows], "totals": totals}


def error_line_to_row(line) -> ReprocessingQuoteRow:
    return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=None, category=line.category,
                                 sell_as_is_value=None, refined_value=None, mineral_value=None, refining_tax=None,
                                 decision="Unknown item", error=line.error)


def _reprocessing_row_to_dict(r: ReprocessingQuoteRow) -> dict:
    return {
        "name": r.name, "quantity": r.quantity, "type_id": r.type_id, "category": r.category,
        "sell_as_is_value": r.sell_as_is_value, "refined_value": r.refined_value, "mineral_value": r.mineral_value,
        "refining_tax": r.refining_tax, "decision": r.decision, "error": r.error,
    }


# ------------------------------------------ Mineral Shopping List (issue #93)
def do_list_refinable_minerals() -> list[dict]:
    """Every distinct mineral/ice product the compressed ore/ice universe can
    actually refine into, name-resolved - what the Mineral Shopping List's
    "add a mineral" picker offers. Derived from real SDE material rows (see
    CLAUDE.md's "Real SDE data drives classification"), not a hardcoded list
    of the eight classic minerals, so ice products and any future ore
    material come along for free."""
    candidates = build_ore_candidate_universe()
    minerals = []
    for type_id in mineral_type_ids_for(candidates):
        row = storage.get_sde_type(type_id)
        if row:
            minerals.append({"type_id": type_id, "name": row[2]})
    minerals.sort(key=lambda m: m["name"])
    return minerals


def do_load_mineral_requirements() -> list[dict]:
    return [{"type_id": type_id, "name": name, "required_qty": qty}
            for type_id, name, qty in storage.load_mineral_requirements()]


def do_save_mineral_requirements(requirements: list[dict]) -> dict:
    """Replaces the whole saved requirement list (see storage.
    replace_mineral_requirements for why replace-all, not upsert). Each entry
    needs a `type_id` that really exists in the SDE cache and a positive
    `required_qty`; the name is always re-resolved from the SDE rather than
    trusted from the caller, so a stale/renamed client can't persist a wrong
    label next to a right type_id."""
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
    storage.replace_mineral_requirements(rows)
    return {"saved": len(rows)}


def _ore_option(candidate, jita_stats, refining_cfg: RefiningConfig,
                 trading_cfg: TradingConfig) -> Optional[OreOption]:
    """Prices one compressed ore/ice candidate and pre-refines one whole
    portion of it, producing a single LP column. Returns None when the type
    can't be used at all (not listed in Jita right now, no SDE portion size,
    or nothing to refine into)."""
    portion_size = storage.get_portion_size(candidate.type_id)
    jita_sell = jita_stats.sell_percentile if jita_stats else None
    unit_cost = landed_cost_per_unit(jita_sell, candidate.volume_m3, trading_cfg)
    if unit_cost is None or not portion_size:
        return None
    # The structure's reprocessing tax is taken out of the refined materials
    # in-game, so it belongs in the yield here, not as a separate ISK fee -
    # see optimizer.py's module docstring (modelling decision 2).
    effective_yield = ore_ice_yield(refining_cfg, candidate.family) * (1 - refining_cfg.refining_tax_rate)
    yield_per_portion = apply_reprocessing_yield(candidate.type_id, portion_size, effective_yield)
    if not yield_per_portion:
        return None
    return OreOption(type_id=candidate.type_id, item=candidate.item, family=candidate.family,
                      is_ice=candidate.is_ice, volume_m3=candidate.volume_m3, portion_size=portion_size,
                      landed_cost_per_unit=unit_cost, yield_per_portion=yield_per_portion)


def do_optimize_mineral_shopping_list(requirements: Optional[list[dict]] = None,
                                       trading_cfg: TradingConfig = TRADING_CONFIG,
                                       refining_cfg: RefiningConfig = REFINING_CONFIG,
                                       production_cfg: ProductionConfig = PRODUCTION_CONFIG,
                                       oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """GitHub issue #93: solves "cheapest way to acquire these minerals" across
    every compressed ore/ice type at once (see refining/optimizer.py for the LP
    itself). `requirements` defaults to the saved list; passing one solves an
    ad-hoc list without persisting it.

    Unlike do_refresh_ore_shortlist/do_quote_reprocessing this needs NO logged-in
    character: every price it reads is either a *buy* price from Jita's public
    regional order book, or (GitHub issue #102) an unauthenticated Goonmetrics
    current-price quote for C-J's own home market - never C-J's authenticated
    structure order book - nothing is sold in this workflow, the minerals are
    consumed by Production. The ore universe is the full SDE-derived one
    (build_ore_candidate_universe), not the Ore Shortlist's active rows: the
    shortlist is a *profit-tracking* selection for the import-and-sell
    business, and excluding an ore from it shouldn't quietly make a build
    list more expensive.

    The ore side always sources from Jita only (ore is imported and refined,
    never bought at home - same as before #102). Only the *direct-mineral*
    alternative (MineralOption.landed_cost_per_unit) now compares Jita-landed
    against C-J's home-market price (GoonmetricsClient.current_prices(
    production_cfg.home_market), the same unauthenticated source Production's
    own home-market quotes already use) and picks whichever is cheaper - a
    mineral already sitting at C-J from other players' reprocessing shouldn't
    be recommended for import just because this function never looked."""
    entries = requirements if requirements is not None else do_load_mineral_requirements()
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

    candidates = build_ore_candidate_universe()
    if not candidates:
        raise ActionError("No compressed ore/ice types found in the SDE cache - run Refresh SDE first.")

    client = ESIClient(trading_cfg, TokenManager(oauth_cfg))
    ore_ids = [c.type_id for c in candidates]
    mineral_ids = [r.type_id for r in wanted]
    try:
        stats_by_id = client.region_order_stats_bulk(trading_cfg.jita_region_id, sorted(set(ore_ids + mineral_ids)))
    except (ESIError, requests.RequestException) as e:
        # region_order_stats_bulk swallows a per-type_id ESI *error response*
        # but not a transport-level failure (ESI down, no route out) - that
        # propagates out of the thread pool, and without this would surface as
        # a bare 500 instead of the app's one user-facing error type.
        raise ActionError(f"Could not fetch Jita's order book ({e}).") from e

    ore_options = [o for o in (_ore_option(c, stats_by_id.get(c.type_id), refining_cfg, trading_cfg)
                               for c in candidates) if o is not None]

    # Best-effort - a Goonmetrics outage shouldn't break the whole shopping
    # list (Jita-only pricing, the pre-#102 behavior, is still a valid
    # fallback), unlike the ESI order-book fetch above which the function's
    # own ore pricing genuinely can't proceed without.
    home_quotes = {}
    if production_cfg.home_market:
        try:
            home_quotes = {p.type_id: p for p in GoonmetricsClient(trading_cfg).current_prices(production_cfg.home_market)}
        except requests.RequestException:
            log.warning("Goonmetrics home-market fetch failed - falling back to Jita-only mineral pricing.")

    mineral_options = {}
    for req in wanted:
        sde_row = storage.get_sde_type(req.type_id)
        volume = sde_row[3] if sde_row and sde_row[3] else 0.0
        stats = stats_by_id.get(req.type_id)
        jita_cost = landed_cost_per_unit(stats.sell_percentile if stats else None, volume, trading_cfg)

        home_quote = home_quotes.get(req.type_id)
        home_cost = (home_quote.sell * (1 + trading_cfg.jita_buy_broker_fee)
                     if home_quote and home_quote.sell > 0 else None)

        if home_cost is not None and (jita_cost is None or home_cost < jita_cost):
            cost, source = home_cost, "Home"
        elif jita_cost is not None:
            cost, source = jita_cost, "Jita"
        else:
            cost, source = None, None

        mineral_options[req.type_id] = MineralOption(
            type_id=req.type_id, name=req.name, landed_cost_per_unit=cost, source=source,
        )

    try:
        plan = optimize_shopping_list(wanted, ore_options, mineral_options)
    except OptimizationError as e:
        raise ActionError(str(e)) from e
    return _plan_to_dict(plan)


def _plan_to_dict(plan: ShoppingListPlan) -> dict:
    return {
        "ore_purchases": [vars(p) for p in plan.ore_purchases],
        "direct_purchases": [vars(p) for p in plan.direct_purchases],
        "coverage": [vars(c) for c in plan.coverage],
        "ore_cost": plan.ore_cost, "direct_cost": plan.direct_cost, "total_cost": plan.total_cost,
        "lp_cost": plan.lp_cost, "all_direct_cost": plan.all_direct_cost,
        "savings_vs_all_direct": plan.savings_vs_all_direct, "total_volume_m3": plan.total_volume_m3,
    }


def do_update_settings(updates: dict, cfg: RefiningConfig = REFINING_CONFIG) -> dict:
    """Persists `updates` to tenant_settings and applies them to the live
    REFINING_CONFIG immediately (see Ore & Minerals Settings tab)."""
    try:
        validate_refining_overrides(updates)
        save_tenant_config_overrides("refining", updates, cfg, cfg_type=RefiningConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return updates

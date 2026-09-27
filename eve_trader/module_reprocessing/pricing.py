"""Per-Module-Shortlist-row profit calculation, and the cheap Discovery-time
estimate - Module Reprocessing Import tool.

Two different price sources, deliberately (see CLAUDE.md's Price Sources
Matrix):
  - Discovery (estimate_discovered_candidates): Goonmetrics *current-price*
    bulk dumps (one HTTP call per market, regardless of how many candidates -
    see goonmetrics_client.GoonmetricsClient.current_prices) - cheap enough
    to run over the whole, large candidate universe. Answers "is this
    candidate worth a closer look", not "what would I actually get right
    now" - never persisted, never used for the real shortlist's own
    Import/Skip decision.
  - Shortlist refresh (evaluate_module_item): real ESI order-book stats
    (esi_client.OrderStats), same 5th-percentile figures Trading's own
    shortlist and Ore & Minerals' Ore Shortlist already use - only for the
    user-curated, bounded set of items actually tracked.

Both share one landed-cost/mineral-value formula (see landed_cost_per_unit
below) so a module's estimated and actual economics are directly
comparable, not computed two different ways that happen to look similar.
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from ..config import TRADING_CONFIG, TradingConfig
from ..esi_client import OrderStats
from ..goonmetrics_client import CurrentPrice
from ..refining.engine import apply_reprocessing_yield
from .config import MODULE_REPROCESSING_CONFIG, ModuleReprocessingConfig
from .engine import scrapmetal_yield
from .models import DiscoveredModuleResult, ModuleCandidate, ModuleShortlistRow

NO_MARKET_DATA_DECISION = "No market data"
SKIP_DECISION = "Skip"
IMPORT_DECISION = "Import"
ALL_DECISIONS = ["Inactive", NO_MARKET_DATA_DECISION, SKIP_DECISION, IMPORT_DECISION]


def landed_cost_per_unit(buy_price: Optional[float], volume_m3: float,
                          trading_cfg: TradingConfig = TRADING_CONFIG,
                          cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG) -> Optional[float]:
    """What one unit really costs, landed at C-J: the purchase-side price
    plus TRADING_CONFIG's own buy-side broker fee (the same real market
    mechanic Ore & Minerals' own landed_cost_per_unit already reuses TRADING_
    CONFIG for - see refining/pricing.py) plus this tool's OWN freight cost
    per m3 (module_reprocessing/config.py's freight_cost_per_m3 - a
    deliberately separate field from TradingConfig.import_cost_per_m3/
    RefiningConfig's own hauling economics, per the user's explicit "own
    config, don't share Ore & Minerals'" requirement). None in means None
    out (nothing listed right now)."""
    if buy_price is None:
        return None
    return buy_price * (1 + trading_cfg.jita_buy_broker_fee) + volume_m3 * cfg.freight_cost_per_m3


def mineral_type_ids_for(candidates: list[ModuleCandidate]) -> list[int]:
    """Every distinct material_type_id any candidate reprocesses into - a
    small, shared set (the classic minerals only, since Faction/Officer/
    Deadspace/rigs - the categories with salvage-only materials - are
    already excluded from this tool's whole candidate universe), worth
    fetching once rather than per-candidate."""
    ids: set[int] = set()
    for c in candidates:
        for material_type_id, _qty in storage.get_type_materials(c.type_id):
            ids.add(material_type_id)
    return sorted(ids)


def _decision(active: bool, have_data: bool, profit: Optional[float], margin: Optional[float],
              cfg: ModuleReprocessingConfig) -> str:
    """Mirrors refining/pricing.py's _decision precedence."""
    if not active:
        return "Inactive"
    if not have_data:
        return NO_MARKET_DATA_DECISION
    if profit is not None and margin is not None and profit > cfg.min_profit_threshold and margin >= cfg.min_margin_threshold:
        return IMPORT_DECISION
    return SKIP_DECISION


def evaluate_module_item(candidate: ModuleCandidate, active: bool,
                          item_stats: Optional[OrderStats], mineral_stats_by_id: dict[int, OrderStats],
                          trading_cfg: TradingConfig = TRADING_CONFIG,
                          cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG) -> ModuleShortlistRow:
    """Pure - `item_stats`/`mineral_stats_by_id` are pre-fetched by the
    caller (see module_reprocessing/actions.py's do_refresh_shortlist), no
    network calls here. Mirrors refining/pricing.py's evaluate_ore_item,
    with scrapmetal_yield instead of ore_ice_yield - apply_reprocessing_yield
    itself already handles portionSize=1 (the common case for a single
    module) the same way it handles ore's portionSize=100, no special-casing
    needed here."""
    portion_size = storage.get_portion_size(candidate.type_id)
    buy_price = item_stats.sell_percentile if item_stats else None
    sell_listed_qty = item_stats.sell_volume if item_stats else None

    if buy_price is None or not portion_size:
        return ModuleShortlistRow(
            item_id=candidate.type_id, item=candidate.item, active=active, volume_m3=candidate.volume_m3,
            landed_cost=None, yield_pct=None, mineral_value=None, refining_tax=None, net_sell=None,
            sell_listed_qty=sell_listed_qty, profit_per_unit=None, margin=None, profit_per_m3=None,
            decision=_decision(active, False, None, None, cfg),
        )

    unit_landed_cost = landed_cost_per_unit(buy_price, candidate.volume_m3, trading_cfg, cfg)
    landed_cost_per_portion = unit_landed_cost * portion_size

    yield_pct = scrapmetal_yield(cfg)
    minerals = apply_reprocessing_yield(candidate.type_id, portion_size, yield_pct)

    mineral_value = 0.0
    have_full_mineral_data = bool(minerals)
    for material_type_id, qty in minerals.items():
        stats = mineral_stats_by_id.get(material_type_id)
        if stats is None or stats.sell_percentile is None:
            have_full_mineral_data = False
            continue
        mineral_value += qty * stats.sell_percentile * trading_cfg.structure_sell_haircut

    if not have_full_mineral_data:
        return ModuleShortlistRow(
            item_id=candidate.type_id, item=candidate.item, active=active, volume_m3=candidate.volume_m3,
            landed_cost=unit_landed_cost, yield_pct=yield_pct, mineral_value=None, refining_tax=None,
            net_sell=None, sell_listed_qty=sell_listed_qty, profit_per_unit=None, margin=None, profit_per_m3=None,
            decision=_decision(active, False, None, None, cfg),
        )

    refining_tax = mineral_value * cfg.refining_tax_rate
    net_sell = mineral_value - refining_tax
    profit_per_portion = net_sell - landed_cost_per_portion
    profit_per_unit = profit_per_portion / portion_size
    margin = profit_per_portion / landed_cost_per_portion if landed_cost_per_portion else None
    profit_per_m3 = (profit_per_unit / candidate.volume_m3) if candidate.volume_m3 else None

    decision = _decision(active, True, profit_per_unit, margin, cfg)

    return ModuleShortlistRow(
        item_id=candidate.type_id, item=candidate.item, active=active, volume_m3=candidate.volume_m3,
        landed_cost=unit_landed_cost, yield_pct=yield_pct, mineral_value=mineral_value, refining_tax=refining_tax,
        net_sell=net_sell, sell_listed_qty=sell_listed_qty, profit_per_unit=profit_per_unit, margin=margin,
        profit_per_m3=profit_per_m3, decision=decision,
    )


def evaluate_module_shortlist(candidates: list[ModuleCandidate], active_by_id: dict[int, bool],
                               item_stats_by_id: dict[int, OrderStats], mineral_stats_by_id: dict[int, OrderStats],
                               trading_cfg: TradingConfig = TRADING_CONFIG,
                               cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG) -> list[ModuleShortlistRow]:
    return [
        evaluate_module_item(c, active_by_id.get(c.type_id, True), item_stats_by_id.get(c.type_id),
                              mineral_stats_by_id, trading_cfg, cfg)
        for c in candidates
    ]


def estimate_discovered_candidate(candidate: ModuleCandidate, jita_price: Optional[CurrentPrice],
                                   mineral_prices_by_id: dict[int, CurrentPrice],
                                   trading_cfg: TradingConfig = TRADING_CONFIG,
                                   cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG,
                                   ) -> DiscoveredModuleResult:
    """Cheap, Discovery-time-only estimate from a Goonmetrics current-price
    bulk dump (CurrentPrice.sell - the going ask, same role ESI's own
    sell_percentile plays in evaluate_module_item, just from a different,
    O(1)-HTTP-calls source - see this module's own docstring). Never used
    for the real shortlist's Import/Skip decision; purely to let the user
    triage a large Discover result set before picking what to track live."""
    portion_size = storage.get_portion_size(candidate.type_id)
    buy_price = jita_price.sell if jita_price else None
    if buy_price is None or not portion_size:
        return DiscoveredModuleResult(type_id=candidate.type_id, item=candidate.item, volume_m3=candidate.volume_m3,
                                       est_landed_cost=None, est_mineral_value=None, est_profit_per_unit=None,
                                       est_margin=None)

    unit_landed_cost = landed_cost_per_unit(buy_price, candidate.volume_m3, trading_cfg, cfg)
    landed_cost_per_portion = unit_landed_cost * portion_size

    yield_pct = scrapmetal_yield(cfg)
    minerals = apply_reprocessing_yield(candidate.type_id, portion_size, yield_pct)

    mineral_value = 0.0
    have_full_mineral_data = bool(minerals)
    for material_type_id, qty in minerals.items():
        price = mineral_prices_by_id.get(material_type_id)
        if price is None or not price.sell:
            have_full_mineral_data = False
            continue
        mineral_value += qty * price.sell * trading_cfg.structure_sell_haircut

    if not have_full_mineral_data:
        return DiscoveredModuleResult(type_id=candidate.type_id, item=candidate.item, volume_m3=candidate.volume_m3,
                                       est_landed_cost=unit_landed_cost, est_mineral_value=None,
                                       est_profit_per_unit=None, est_margin=None)

    refining_tax = mineral_value * cfg.refining_tax_rate
    net_sell = mineral_value - refining_tax
    profit_per_portion = net_sell - landed_cost_per_portion
    profit_per_unit = profit_per_portion / portion_size
    margin = profit_per_portion / landed_cost_per_portion if landed_cost_per_portion else None

    return DiscoveredModuleResult(type_id=candidate.type_id, item=candidate.item, volume_m3=candidate.volume_m3,
                                   est_landed_cost=unit_landed_cost, est_mineral_value=mineral_value,
                                   est_profit_per_unit=profit_per_unit, est_margin=margin)


def estimate_discovered_candidates(candidates: list[ModuleCandidate], jita_prices_by_id: dict[int, CurrentPrice],
                                    mineral_prices_by_id: dict[int, CurrentPrice],
                                    trading_cfg: TradingConfig = TRADING_CONFIG,
                                    cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG,
                                    ) -> list[DiscoveredModuleResult]:
    return [
        estimate_discovered_candidate(c, jita_prices_by_id.get(c.type_id), mineral_prices_by_id, trading_cfg, cfg)
        for c in candidates
    ]

"""Per-Ore-Shortlist-row profit calculation - GitHub issue #91.

    Landed Cost   = Jita sell percentile x (1 + jita_buy_broker_fee) + volume x import_cost_per_m3
    Mineral Yield = one whole portion's worth of minerals, via refining/engine.py's
                    ore_ice_yield (this item's family) + apply_reprocessing_yield
    Mineral Value = sum(mineral_qty x C-J sell percentile x structure_sell_haircut)
    Refining Tax  = Mineral Value x RefiningConfig.refining_tax_rate
    Net Sell      = Mineral Value - Refining Tax
    Profit        = Net Sell - (Landed Cost x portion_size), normalized back to per-unit

Reuses TRADING_CONFIG for jita_region_id/structure_id/broker-fee/haircut/haul-
cost - Ore Shortlist buys at the same Jita and sells (refined minerals) at the
same C-J structure Trading/Production already use, confirmed with the user
during planning rather than duplicating those fields onto RefiningConfig.
Portion-size rounding means yield is computed for one whole portion, not per
unit (a single unit almost never clears a real portionSize, e.g. Veldspar's
100) - both landed cost and profit are then normalized back to a per-unit
figure so this reports the same shape as Trading's own Shortlist (profit/
unit, margin, profit/m3).
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from ..config import TRADING_CONFIG, TradingConfig
from ..esi_client import OrderStats
from .config import REFINING_CONFIG, RefiningConfig
from .engine import apply_reprocessing_yield, ore_ice_yield
from .models import OreCandidate, OreShortlistRow

NO_MARKET_DATA_DECISION = "No market data"
SKIP_DECISION = "Skip"
IMPORT_DECISION = "Import"
ALL_DECISIONS = ["Inactive", NO_MARKET_DATA_DECISION, SKIP_DECISION, IMPORT_DECISION]


def mineral_type_ids_for(candidates: list[OreCandidate]) -> list[int]:
    """Every distinct material_type_id any candidate's portion-size batch
    reprocesses into - a small, shared set (~15-20 minerals/ice products
    total) worth fetching once via structure_order_stats_bulk, rather than
    per-candidate."""
    ids: set[int] = set()
    for c in candidates:
        for material_type_id, _qty in storage.get_type_materials(c.type_id):
            ids.add(material_type_id)
    return sorted(ids)


def _decision(active: bool, have_data: bool, profit: Optional[float], margin: Optional[float],
              cfg: TradingConfig) -> str:
    """Mirrors shortlist._decision's precedence, minus "Already ordered" -
    Ore Shortlist has no own-orders/buyer-covered tracking (confirmed out of
    scope for this phase)."""
    if not active:
        return "Inactive"
    if not have_data:
        return NO_MARKET_DATA_DECISION
    if profit is not None and margin is not None and profit > cfg.min_profit_threshold and margin >= cfg.min_margin_threshold:
        return IMPORT_DECISION
    return SKIP_DECISION


def evaluate_ore_item(candidate: OreCandidate, active: bool,
                       jita_stats: Optional[OrderStats], mineral_stats_by_id: dict[int, OrderStats],
                       trading_cfg: TradingConfig = TRADING_CONFIG,
                       refining_cfg: RefiningConfig = REFINING_CONFIG) -> OreShortlistRow:
    """Pure - `jita_stats`/`mineral_stats_by_id` are pre-fetched by the caller
    (see evaluate_ore_shortlist), no network calls here."""
    portion_size = storage.get_portion_size(candidate.type_id)
    jita_sell = jita_stats.sell_percentile if jita_stats else None
    sell_listed_qty = jita_stats.sell_volume if jita_stats else None  # Jita liquidity: how much is available to buy

    if jita_sell is None or not portion_size:
        return OreShortlistRow(
            item_id=candidate.type_id, item=candidate.item, family=candidate.family, is_ice=candidate.is_ice,
            active=active, volume_m3=candidate.volume_m3, landed_cost=None, yield_pct=None, mineral_value=None,
            refining_tax=None, net_sell=None, sell_listed_qty=sell_listed_qty, profit_per_unit=None, margin=None,
            profit_per_m3=None, decision=_decision(active, False, None, None, trading_cfg),
        )

    landed_cost_per_unit = jita_sell * (1 + trading_cfg.jita_buy_broker_fee) + candidate.volume_m3 * trading_cfg.import_cost_per_m3
    landed_cost_per_portion = landed_cost_per_unit * portion_size

    yield_pct = ore_ice_yield(refining_cfg, candidate.family)
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
        return OreShortlistRow(
            item_id=candidate.type_id, item=candidate.item, family=candidate.family, is_ice=candidate.is_ice,
            active=active, volume_m3=candidate.volume_m3, landed_cost=landed_cost_per_unit, yield_pct=yield_pct,
            mineral_value=None, refining_tax=None, net_sell=None, sell_listed_qty=sell_listed_qty,
            profit_per_unit=None, margin=None, profit_per_m3=None,
            decision=_decision(active, False, None, None, trading_cfg),
        )

    refining_tax = mineral_value * refining_cfg.refining_tax_rate
    net_sell = mineral_value - refining_tax
    profit_per_portion = net_sell - landed_cost_per_portion
    profit_per_unit = profit_per_portion / portion_size
    margin = profit_per_portion / landed_cost_per_portion if landed_cost_per_portion else None
    profit_per_m3 = (profit_per_unit / candidate.volume_m3) if candidate.volume_m3 else None

    decision = _decision(active, True, profit_per_portion, margin, trading_cfg)

    return OreShortlistRow(
        item_id=candidate.type_id, item=candidate.item, family=candidate.family, is_ice=candidate.is_ice,
        active=active, volume_m3=candidate.volume_m3, landed_cost=landed_cost_per_unit, yield_pct=yield_pct,
        mineral_value=mineral_value, refining_tax=refining_tax, net_sell=net_sell,
        sell_listed_qty=sell_listed_qty, profit_per_unit=profit_per_unit, margin=margin,
        profit_per_m3=profit_per_m3, decision=decision,
    )


def evaluate_ore_shortlist(candidates: list[OreCandidate], active_by_id: dict[int, bool],
                            jita_stats_by_id: dict[int, OrderStats], mineral_stats_by_id: dict[int, OrderStats],
                            trading_cfg: TradingConfig = TRADING_CONFIG,
                            refining_cfg: RefiningConfig = REFINING_CONFIG) -> list[OreShortlistRow]:
    return [
        evaluate_ore_item(c, active_by_id.get(c.type_id, True), jita_stats_by_id.get(c.type_id),
                           mineral_stats_by_id, trading_cfg, refining_cfg)
        for c in candidates
    ]

"""Per-shortlist-row margin calculation:

    Landed Cost      = Jita sell percentile x (1 + jita_buy_broker_fee) + Volume x import_cost_per_m3
    C-J Net Sell     = Structure sell percentile x haircut
    Profit / Unit    = Net Sell - Landed Cost
    Margin           = Profit / Landed Cost
    Profit / m3      = Profit / Volume
    Decision         = Inactive | Missing ID | No market data | Skip |
                       Already ordered | Import
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from .config import TRADING_CONFIG, TradingConfig
from .esi_client import OrderStats
from .goonmetrics_client import HistoryPoint
from .models import ShortlistItem, ShortlistRow

log = logging.getLogger(__name__)

# Split 2026-08-18 from one merged "No market data / Skip" label - confirmed
# real gap: a brand-new candidate with zero C-J sell history (never even
# priced yet) and an item that's been fully evaluated but just isn't
# profitable enough looked identical to the user, even though the right next
# action differs (seed the market vs. accept it's not viable). Both still
# feed the same skip-grace-period deactivation streak (see actions.py's
# SKIP_STREAK_DECISIONS) - only the label shown to the user changes.
NO_MARKET_DATA_DECISION = "No market data"
SKIP_DECISION = "Skip"
ALL_DECISIONS = ["Inactive", "Missing ID", NO_MARKET_DATA_DECISION, SKIP_DECISION, "Already ordered", "Import"]


def _decision(active: bool, item_id: Optional[int], sell_volume: Optional[float],
              profit: Optional[float], margin: Optional[float],
              own_orders_remaining: float, buyer_already_covered: bool, cfg: TradingConfig) -> str:
    """Precedence, most restrictive first: an inactive item is always
    "Inactive" regardless of everything else; a missing item_id can't be
    priced at all, so it's next; only then does a genuine data gap
    ("No market data" - sell_volume/profit/margin never came back at all,
    e.g. no C-J listing has ever existed for this item) get checked, ahead
    of the actual buy decision; a real, priced item that simply doesn't
    clear the profit/margin bar is "Skip" instead - distinct from "No market
    data" since the right next action differs (seed the market vs. accept
    it's not viable). "Already ordered" only applies once an item has
    otherwise cleared the Import bar (it's a refinement of Import - "you'd
    import this, but you already have it covered" - not an independent
    state)."""
    if not active:
        return "Inactive"
    if not item_id:
        return "Missing ID"
    have_data = sell_volume is not None and profit is not None and margin is not None
    if not have_data:
        return NO_MARKET_DATA_DECISION
    if sell_volume > 0 and profit > cfg.min_profit_threshold and margin >= cfg.min_margin_threshold:
        return "Already ordered" if (own_orders_remaining > 0 or buyer_already_covered) else "Import"
    return SKIP_DECISION


def evaluate_shortlist_item(item: ShortlistItem, own_orders_remaining: float,
                             jita_stats: Optional[OrderStats], structure_stats: Optional[OrderStats],
                             cfg: TradingConfig = TRADING_CONFIG, buyer_already_covered: bool = False,
                             avg_daily_volume: Optional[float] = None) -> ShortlistRow:
    """Computes landed cost, net sell, margin, and decision for a single
    shortlist item. `jita_stats`/`structure_stats` are pre-fetched (see evaluate_shortlist) -
    this function itself makes no network calls. `buyer_already_covered`:
    the buyer already has an open buy order or inventory for this item at
    Jita or at the destination structure (see
    own_orders.fetch_buyer_already_covered) - also counts as "Already
    ordered", same as the seller already having it listed. `avg_daily_volume`
    (see average_market_daily_volume) is the item's real observed average
    daily *market-wide* traded quantity in C-J's own home region - not the
    buyer/seller's own personal sales (GitHub issue #51 originally computed
    this from trade_reconciliation.average_daily_sold_by_type, the trader's
    own realized_trades matches; GitHub issue #100 replaced that with real
    region-wide Goonmetrics history, since "Profit / Day" is meant to be
    CLAUDE.md's documented "whole day of market turnover" theoretical
    ceiling, not something scoped to one seller - a never-actually-sold
    candidate item can never have personal sales, so #51's version left
    "Profit / Day" empty for exactly the rows a shortlist is for). None (the
    default) means Goonmetrics has no history for this item in that region.

    An inactive item still gets fully priced below (GitHub issue #6,
    confirmed real gap: margin/trend/profit used to go blank the moment an
    item was deactivated) - only a genuinely unpriceable item (no item_id at
    all) short-circuits here. `_decision` still independently returns
    "Inactive" first regardless of these pricing fields, so the Status
    column is unaffected either way."""
    if not item.item_id:
        return ShortlistRow(
            item=item.item, category=item.category, landed_cost=None, net_sell=None,
            sell_volume=None, own_orders_remaining=own_orders_remaining,
            profit_per_unit=None, margin=None, profit_per_m3=None,
            decision=_decision(item.active, item.item_id, None, None, None,
                                own_orders_remaining, buyer_already_covered, cfg),
            active=item.active, item_id=item.item_id, volume_m3=item.volume_m3,
            jita_sell=None, import_cost=None, meta_level=item.meta_level,
            avg_daily_volume=avg_daily_volume,
        )

    # Deliberately sell_percentile (the ask price, an instant-buy fill), not
    # buy_percentile - even though real purchases go through a standing buy
    # order in the order book (which is why jita_buy_broker_fee still
    # applies - EVE only charges broker's fee on standing orders, confirmed
    # against wiki.eveuniversity.org/Tax). Confirmed with the user: pricing
    # off the ask deliberately keeps the margin estimate conservative, since
    # a lowball bid at buy_percentile might not fill at all before the
    # opportunity is gone - not an oversight.
    jita_sell = jita_stats.sell_percentile if jita_stats else None
    import_cost = item.volume_m3 * cfg.import_cost_per_m3
    landed_cost = (jita_sell * (1 + cfg.jita_buy_broker_fee) + import_cost) if jita_sell is not None else None
    net_sell = (structure_stats.sell_percentile * cfg.structure_sell_haircut) \
        if structure_stats and structure_stats.sell_percentile is not None else None
    sell_volume = structure_stats.sell_volume if structure_stats else None

    profit = (net_sell - landed_cost) if (net_sell is not None and landed_cost is not None) else None
    margin = (profit / landed_cost) if (profit is not None and landed_cost not in (None, 0)) else None
    profit_m3 = (profit / item.volume_m3) if (profit is not None and item.volume_m3 > 0) else None

    decision = _decision(item.active, item.item_id, sell_volume, profit, margin,
                          own_orders_remaining, buyer_already_covered, cfg)

    return ShortlistRow(
        item=item.item, category=item.category, landed_cost=landed_cost, net_sell=net_sell,
        sell_volume=sell_volume, own_orders_remaining=own_orders_remaining,
        profit_per_unit=profit, margin=margin, profit_per_m3=profit_m3,
        decision=decision, active=item.active, item_id=item.item_id,
        volume_m3=item.volume_m3, jita_sell=jita_sell, import_cost=import_cost,
        meta_level=item.meta_level, avg_daily_volume=avg_daily_volume,
    )


def evaluate_shortlist(items: list[ShortlistItem], own_orders_by_item: dict[int, float],
                        jita_stats_by_item: dict[int, OrderStats], structure_stats_by_item: dict[int, OrderStats],
                        cfg: TradingConfig = TRADING_CONFIG,
                        buyer_already_covered_ids: frozenset[int] = frozenset(),
                        avg_daily_volume_by_item: Optional[dict[int, float]] = None) -> list[ShortlistRow]:
    """Recomputes every shortlist row's margin/decision in one pass.
    `jita_stats_by_item`/`structure_stats_by_item` are pre-fetched once for
    every active item_id in `items` (see ESIClient.region_order_stats_bulk/
    structure_order_stats_bulk) - this function makes no network calls
    itself, unlike the old per-item-call version which re-downloaded the
    entire structure order book on every single item. `avg_daily_volume_by_item`
    (see average_market_daily_volume) feeds each row's avg_daily_volume -
    GitHub issue #100."""
    avg_daily_volume_by_item = avg_daily_volume_by_item or {}
    rows = []
    for item in items:
        remaining = own_orders_by_item.get(item.item_id, 0.0)
        jita_stats = jita_stats_by_item.get(item.item_id) if item.item_id else None
        structure_stats = structure_stats_by_item.get(item.item_id) if item.item_id else None
        buyer_covered = item.item_id in buyer_already_covered_ids
        avg_daily_volume = avg_daily_volume_by_item.get(item.item_id) if item.item_id else None
        rows.append(evaluate_shortlist_item(item, remaining, jita_stats, structure_stats, cfg, buyer_covered,
                                             avg_daily_volume))
    return rows


def average_market_daily_volume(history_points: list[HistoryPoint]) -> dict[int, float]:
    """Real average daily *market-wide* traded quantity per type_id, from
    Goonmetrics' region history (see actions.py's _refresh_shortlist_rows,
    which fetches `history_points` for cfg.reference_region_id - the real
    region C-J's own solar system sits in, confirmed live 2026-08-23 via
    sde_solar_systems; there's no ESI/Goonmetrics history endpoint for a
    player structure's own market at all, region-wide is the closest real
    signal available). Averages `movement` (confirmed live: a genuine unit
    count, not an ISK value - see goonmetrics_client.HistoryPoint's own
    field) over every day Goonmetrics returned (its price_history endpoint
    returns a fixed ~28-day window), not just a recent slice - matches
    history_backtest.py's own "average over every paired day available"
    approach for the same reference region.

    GitHub issue #100: this - not the buyer/seller's own realized_trades
    (GitHub issue #51's trade_reconciliation.average_daily_sold_by_type) -
    is what ShortlistRow.avg_daily_volume (and therefore "Profit / Day") is
    computed from. #51 fixed a real bug (order-book *depth* wildly
    overstating "Profit / Day" for a never-actually-sold item) but
    overcorrected into scoping it to one trader's own sales, which left
    "Profit / Day" empty for every prospective import candidate (the exact
    rows a shortlist exists to evaluate, which by definition haven't been
    sold by this trader yet) - CLAUDE.md's own "Theoretical ceiling figures"
    section already documented the intended whole-market-turnover meaning,
    #51 just never updated the implementation to match it correctly."""
    by_type: dict[int, list[float]] = defaultdict(list)
    for point in history_points:
        by_type[point.type_id].append(point.movement)
    return {type_id: sum(movements) / len(movements) for type_id, movements in by_type.items() if movements}


def summary_counts(rows: list[ShortlistRow]) -> dict[str, float]:
    """Equivalent of the G1:H6 summary block."""
    import_candidates = sum(1 for r in rows if r.decision == "Import")
    already_ordered = sum(1 for r in rows if r.decision == "Already ordered")
    skipped = sum(1 for r in rows if "Skip" in r.decision)
    positive_margin = sum(1 for r in rows if r.margin is not None and r.margin > 0)
    margins = [r.margin for r in rows if r.margin is not None and r.margin > 0]
    avg_margin = sum(margins) / len(margins) if margins else None
    return {
        "import_candidates": import_candidates,
        "already_ordered": already_ordered,
        "skipped": skipped,
        "positive_margin": positive_margin,
        "avg_margin": avg_margin,
    }


def top_imports_by_daily_profit(rows: list[ShortlistRow], top_n: int = 10) -> list[dict]:
    """Equivalent of the J:N 'Top Imports: Max. Gewinn / Tag' block:
    Gewinn / Tag = Profit / Unit x real average daily *market-wide* traded
    quantity (`avg_daily_volume` - see average_market_daily_volume, sourced
    from Goonmetrics region history, NOT `sell_volume`/order-book depth and
    NOT the trader's own realized sales - GitHub issue #100). An item
    Goonmetrics has no history for yet (avg_daily_volume is None) is
    correctly excluded here rather than estimated from something else."""
    out = []
    for r in rows:
        if r.profit_per_unit is None or r.avg_daily_volume is None or r.margin is None:
            continue
        out.append({
            "item": r.item, "profit_per_unit": r.profit_per_unit, "margin": r.margin,
            "avg_daily_volume": r.avg_daily_volume, "max_profit_per_day": r.profit_per_unit * r.avg_daily_volume,
            "decision": r.decision,
        })
    out.sort(key=lambda x: x["max_profit_per_day"], reverse=True)
    return out[:top_n]


def audit_shortlist(items: list[ShortlistItem]) -> dict[str, int]:
    """Equivalent of the R:U 'Manual Repair' QA block (duplicate/missing IDs, bad volumes)."""
    ids = [i.item_id for i in items if i.item_id]
    duplicates = len(ids) - len(set(ids))
    missing_ids = sum(1 for i in items if not i.item_id)
    bad_volume = sum(1 for i in items if i.volume_m3 is None or i.volume_m3 <= 0)
    return {"duplicate_type_ids": duplicates, "missing_type_ids": missing_ids, "invalid_volume": bad_volume}

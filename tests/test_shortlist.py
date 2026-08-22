from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.models import ShortlistItem
from eve_trader.shortlist import audit_shortlist, evaluate_shortlist_item, summary_counts, top_imports_by_daily_profit


def test_profitable_item_is_recommended_for_import():
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95,
                         jita_buy_broker_fee=0.0, min_margin_threshold=0.05)
    item = ShortlistItem(item="Test Widget", item_id=123, category="Module/Rig", volume_m3=0.1, active=True)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)
    structure = OrderStats(sell_percentile=2000.0, sell_volume=50, buy_percentile=1800.0, buy_volume=10)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=jita, structure_stats=structure, cfg=cfg)

    # landed = 1000*(1+0) + 0.1*900 = 1090 ; net = 2000*0.95 = 1900 ; profit = 810 ; margin ~= 0.743
    assert row.landed_cost == 1090.0
    assert row.net_sell == 1900.0
    assert round(row.profit_per_unit, 2) == 810.0
    assert row.decision == "Import"


def test_landed_cost_includes_buy_broker_fee():
    cfg = TradingConfig(import_cost_per_m3=900.0, jita_buy_broker_fee=0.0147)
    item = ShortlistItem(item="Test Widget", item_id=123, category="Module/Rig", volume_m3=0.1, active=True)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=jita, structure_stats=None, cfg=cfg)

    # landed = 1000*1.0147 + 0.1*900 = 1104.7
    assert round(row.landed_cost, 2) == 1104.7


def test_already_ordered_when_own_orders_remain():
    cfg = TradingConfig()
    item = ShortlistItem(item="Test Widget", item_id=123, category="Module/Rig", volume_m3=0.1, active=True)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)
    structure = OrderStats(sell_percentile=2000.0, sell_volume=50, buy_percentile=1800.0, buy_volume=10)

    row = evaluate_shortlist_item(item, own_orders_remaining=25.0,
                                   jita_stats=jita, structure_stats=structure, cfg=cfg)
    assert row.decision == "Already ordered"


def test_inactive_item_still_decision_inactive_when_stats_absent():
    cfg = TradingConfig()
    item = ShortlistItem(item="Disabled", item_id=999, category="Material", volume_m3=1.0, active=False)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=None, structure_stats=None, cfg=cfg)
    assert row.decision == "Inactive"
    assert row.landed_cost is None  # no stats were supplied at all, not because it's inactive


def test_inactive_item_still_gets_priced_when_stats_are_supplied():
    # GitHub issue #6: margin/trend/profit used to go blank the moment an
    # item was deactivated, even when real market data was available - only
    # a genuinely unpriceable item (no item_id) should short-circuit.
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95, jita_buy_broker_fee=0.0)
    item = ShortlistItem(item="Disabled but priceable", item_id=999, category="Material", volume_m3=0.1, active=False)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)
    structure = OrderStats(sell_percentile=2000.0, sell_volume=50, buy_percentile=1800.0, buy_volume=10)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=jita, structure_stats=structure, cfg=cfg)

    assert row.decision == "Inactive"
    assert row.landed_cost == 1090.0
    assert row.net_sell == 1900.0
    assert round(row.profit_per_unit, 2) == 810.0
    assert row.margin is not None


def test_missing_item_id_still_short_circuits_regardless_of_active():
    cfg = TradingConfig()
    item = ShortlistItem(item="No ID", item_id=0, category="Material", volume_m3=1.0, active=True)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=None, structure_stats=None, cfg=cfg)
    assert row.decision == "Missing ID"
    assert row.landed_cost is None


def test_missing_market_data_is_no_market_data():
    cfg = TradingConfig()
    item = ShortlistItem(item="No Market", item_id=42, category="Material", volume_m3=1.0, active=True)
    jita = OrderStats(sell_percentile=None, sell_volume=0, buy_percentile=None, buy_volume=0)
    structure = OrderStats(sell_percentile=None, sell_volume=0, buy_percentile=None, buy_volume=0)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=jita, structure_stats=structure, cfg=cfg)
    assert row.decision == "No market data"


def test_priced_but_unprofitable_item_is_skip():
    # Distinct from "No market data" (see shortlist.py's 2026-08-18 split) -
    # this item has real jita/structure quotes, they just don't clear the
    # margin bar, so the right label is "Skip", not "No market data".
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95,
                         jita_buy_broker_fee=0.0, min_margin_threshold=0.05)
    item = ShortlistItem(item="Test Widget", item_id=123, category="Module/Rig", volume_m3=0.1, active=True)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)
    structure = OrderStats(sell_percentile=1000.0, sell_volume=50, buy_percentile=900.0, buy_volume=10)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=jita, structure_stats=structure, cfg=cfg)
    assert row.decision == "Skip"


def test_avg_daily_sold_flows_through_to_the_row():
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95, jita_buy_broker_fee=0.0)
    item = ShortlistItem(item="Test Widget", item_id=123, category="Module/Rig", volume_m3=0.1, active=True)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)
    structure = OrderStats(sell_percentile=2000.0, sell_volume=50, buy_percentile=1800.0, buy_volume=10)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0, jita_stats=jita, structure_stats=structure,
                                   cfg=cfg, avg_daily_sold=3.5)
    assert row.avg_daily_sold == 3.5


def test_avg_daily_sold_defaults_to_none_when_not_supplied():
    # GitHub issue #51: no real sale ever matched for this item - must stay
    # None, not silently fall back to sell_volume/order-book depth.
    cfg = TradingConfig()
    item = ShortlistItem(item="Test Widget", item_id=123, category="Module/Rig", volume_m3=0.1, active=True)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)
    structure = OrderStats(sell_percentile=2000.0, sell_volume=999999.0, buy_percentile=1800.0, buy_volume=10)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0, jita_stats=jita, structure_stats=structure, cfg=cfg)
    assert row.sell_volume == 999999.0  # a huge listed quantity...
    assert row.avg_daily_sold is None   # ...must not leak into the real-sales figure


def test_top_imports_excludes_items_with_no_real_sales_data():
    # GitHub issue #51 (real bug, not just a labeling issue): a
    # never-actually-sold item with a huge order book used to show a wildly
    # inflated "Profit / Day" because that was computed from sell_volume.
    # Now it's excluded entirely when avg_daily_sold is None, rather than
    # estimated from listed quantity.
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95, jita_buy_broker_fee=0.0)
    item = ShortlistItem(item="Never Sold", item_id=123, category="Module/Rig", volume_m3=0.1, active=True)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)
    structure = OrderStats(sell_percentile=2000.0, sell_volume=999999.0, buy_percentile=1800.0, buy_volume=10)
    row = evaluate_shortlist_item(item, own_orders_remaining=0.0, jita_stats=jita, structure_stats=structure, cfg=cfg)

    assert top_imports_by_daily_profit([row]) == []


def test_top_imports_uses_avg_daily_sold_not_sell_volume():
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95, jita_buy_broker_fee=0.0)
    item = ShortlistItem(item="Real Seller", item_id=123, category="Module/Rig", volume_m3=0.1, active=True)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=500, buy_percentile=900.0, buy_volume=300)
    # A small listed order-book quantity (5) but real observed daily sales of 20 -
    # the result must be driven by the latter, not the former.
    structure = OrderStats(sell_percentile=2000.0, sell_volume=5, buy_percentile=1800.0, buy_volume=10)
    row = evaluate_shortlist_item(item, own_orders_remaining=0.0, jita_stats=jita, structure_stats=structure,
                                   cfg=cfg, avg_daily_sold=20.0)

    result = top_imports_by_daily_profit([row])
    assert len(result) == 1
    # profit_per_unit = 1900 - 1090 = 810 ; 810 * 20 = 16200 (not 810 * 5 = 4050)
    assert round(result[0]["max_profit_per_day"], 2) == 16200.0


def test_summary_counts_and_audit():
    cfg = TradingConfig()
    items = [
        ShortlistItem(item="A", item_id=1, category="Module/Rig", volume_m3=0.1),
        ShortlistItem(item="B", item_id=1, category="Module/Rig", volume_m3=0.1),  # duplicate id
        ShortlistItem(item="C", item_id=0, category="Material", volume_m3=-1),      # missing id + bad volume
    ]
    audit = audit_shortlist(items)
    assert audit["duplicate_type_ids"] == 1
    assert audit["missing_type_ids"] == 1
    assert audit["invalid_volume"] == 1

from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.models import ShortlistItem
from eve_trader.shortlist import audit_shortlist, evaluate_shortlist_item, summary_counts


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


def test_inactive_item_short_circuits_without_stats():
    cfg = TradingConfig()
    item = ShortlistItem(item="Disabled", item_id=999, category="Material", volume_m3=1.0, active=False)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=None, structure_stats=None, cfg=cfg)
    assert row.decision == "Inactive"
    assert row.landed_cost is None


def test_missing_market_data_is_skip():
    cfg = TradingConfig()
    item = ShortlistItem(item="No Market", item_id=42, category="Material", volume_m3=1.0, active=True)
    jita = OrderStats(sell_percentile=None, sell_volume=0, buy_percentile=None, buy_volume=0)
    structure = OrderStats(sell_percentile=None, sell_volume=0, buy_percentile=None, buy_volume=0)

    row = evaluate_shortlist_item(item, own_orders_remaining=0.0,
                                   jita_stats=jita, structure_stats=structure, cfg=cfg)
    assert row.decision == "No market data / Skip"


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

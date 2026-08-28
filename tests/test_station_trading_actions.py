from eve_trader import storage
from eve_trader.esi_client import OrderStats
from eve_trader.station_trading import actions
from eve_trader.station_trading.config import StationTradingConfig


def _cfg(**overrides):
    cfg = StationTradingConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def test_profit_deducts_broker_fee_both_legs_and_sales_tax_sell_leg_only():
    cfg = _cfg(broker_fee_rate=0.05, sales_tax_rate=0.10)
    profit_per_unit, margin = actions._profit(live_buy=100.0, live_sell=120.0, cfg=cfg)

    buy_cost = 100.0 * 1.05
    sell_net = 120.0 * (1 - 0.05 - 0.10)
    assert profit_per_unit == sell_net - buy_cost
    assert margin == (sell_net - buy_cost) / buy_cost


def test_profit_is_none_without_a_live_price():
    cfg = _cfg()
    assert actions._profit(None, 120.0, cfg) == (None, None)
    assert actions._profit(100.0, None, cfg) == (None, None)
    assert actions._profit(0.0, 120.0, cfg) == (None, None)


def test_build_shortlist_rows_includes_category_and_live_profit(monkeypatch):
    cfg = _cfg(broker_fee_rate=0.0, sales_tax_rate=0.0)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 0, "Tritanium", 0.01, 0, 0, 0, 0))
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 4)
    monkeypatch.setattr(storage, "load_sde_category_names", lambda: {4: "Mineral"})
    monkeypatch.setattr(actions, "confirm_live",
                         lambda type_ids: {34: OrderStats(sell_percentile=6.0, sell_volume=1.0,
                                                           buy_percentile=5.0, buy_volume=1.0)})

    rows = actions._build_shortlist_rows([(34, 0.1, 5000.0, "2026-08-27T00:00:00", True)], cfg)

    assert rows == [{
        "type_id": 34, "name": "Tritanium", "category": "Mineral", "spread_pct": 0.1,
        "avg_daily_volume": 5000.0, "discovered_at": "2026-08-27T00:00:00", "active": True,
        "live_buy": 5.0, "live_sell": 6.0, "profit_per_unit": 1.0, "margin": 0.2, "profit_per_day": 5000.0,
    }]


def test_build_shortlist_rows_unknown_category_when_type_not_in_sde(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: None)
    monkeypatch.setattr(storage, "load_sde_category_names", lambda: {})
    monkeypatch.setattr(actions, "confirm_live", lambda type_ids: {})

    rows = actions._build_shortlist_rows([(999, 0.1, 5000.0, "2026-08-27T00:00:00", True)], cfg)

    assert rows[0]["category"] == "Unknown"
    assert rows[0]["name"] == "999"
    assert rows[0]["live_buy"] is None and rows[0]["profit_per_unit"] is None
    assert rows[0]["profit_per_day"] is None

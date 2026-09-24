from datetime import date

import pandas as pd

from eve_trader import portfolio, storage
from eve_trader.config import TradingConfig
from eve_trader.models import RealizedTrade

_COLUMNS = [
    "type_id", "item", "buy_date", "buy_qty", "buy_unit_price", "sell_date",
    "sell_qty", "sell_unit_price", "matched_qty", "realized_profit", "margin", "run_ts",
]


def _trades_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_COLUMNS)


def _row(type_id, run_ts, profit, buy_price=100.0, qty=1, sell_date="2026-01-01T12:00:00Z"):
    return {
        "type_id": type_id, "item": f"Item {type_id}", "buy_date": "2026-01-01T00:00:00Z",
        "buy_qty": qty, "buy_unit_price": buy_price, "sell_date": sell_date, "sell_qty": qty,
        "sell_unit_price": buy_price + profit, "matched_qty": qty, "realized_profit": profit,
        "margin": profit / buy_price, "run_ts": run_ts,
    }


def test_no_data_at_all_returns_zeros(monkeypatch):
    monkeypatch.setattr(storage, "read_table", lambda table: _trades_df([]))
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])

    result = portfolio.portfolio_overview(TradingConfig())

    assert result["trading_realized_profit"] == 0.0
    assert result["trading_average_margin"] == 0.0
    assert result["trading_daily_profit_volatility"] is None
    assert result["trading_trade_count"] == 0
    assert result["production_stock_value"] == 0.0
    assert result["production_stock_targets_configured"] is False
    assert result["combined_value"] == 0.0


def test_sums_realized_profit_from_latest_run_only(monkeypatch):
    df = _trades_df([
        # Older, stale run - must be ignored entirely.
        _row(1, "run1", 999.0),
        # Latest run, split across two sell-days - this is what should be summed.
        _row(1, "run2", 100.0, sell_date="2026-01-02T12:00:00Z"),
        _row(2, "run2", 50.0, sell_date="2026-01-03T12:00:00Z"),
    ])
    monkeypatch.setattr(storage, "read_table", lambda table: df)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])

    result = portfolio.portfolio_overview(TradingConfig())

    assert result["trading_realized_profit"] == 150.0
    assert result["trading_trade_count"] == 2
    # Two distinct sell-days with different daily totals - a real, non-zero spread.
    assert result["trading_daily_profit_volatility"] is not None
    assert result["trading_daily_profit_volatility"] > 0


def test_single_day_of_trades_has_no_volatility(monkeypatch):
    df = _trades_df([_row(1, "run1", 50.0)])
    monkeypatch.setattr(storage, "read_table", lambda table: df)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])

    result = portfolio.portfolio_overview(TradingConfig())

    assert result["trading_daily_profit_volatility"] is None
    assert result["trading_realized_profit"] == 50.0


def test_production_stock_value_included_when_targets_configured(monkeypatch):
    monkeypatch.setattr(storage, "read_table", lambda table: _trades_df([]))
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(1, "Widget", 0, None, None)])

    from eve_trader.production import engine
    monkeypatch.setattr(engine, "stock_value", lambda cfg: {"total_value": 12345.0, "priced_items": 1, "unpriced_items": 0})

    result = portfolio.portfolio_overview(TradingConfig())

    assert result["production_stock_targets_configured"] is True
    assert result["production_stock_value"] == 12345.0
    assert result["combined_value"] == 12345.0


def test_take_portfolio_snapshot_upserts_today_with_wealth_fields_none(monkeypatch):
    monkeypatch.setattr(storage, "read_table", lambda table: _trades_df([]))
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    upserted = {}
    monkeypatch.setattr(storage, "upsert_portfolio_snapshot",
                        lambda snapshot_date, values: upserted.update(date=snapshot_date, **values))

    result = portfolio.take_portfolio_snapshot(TradingConfig())

    assert upserted["date"] == date.today()
    assert upserted["total_wealth"] is None
    assert upserted["wealth_assets_value"] is None
    assert upserted["wealth_wallet_balance"] is None
    assert upserted["combined_value"] == 0.0
    assert result["total_wealth"] is None
    assert result["combined_value"] == 0.0


def test_do_get_portfolio_overview_takes_snapshot_when_not_taken_today(monkeypatch):
    monkeypatch.setattr(storage, "latest_portfolio_snapshot_date", lambda: None)
    calls = []
    monkeypatch.setattr(portfolio, "take_portfolio_snapshot", lambda cfg: calls.append("snapshot") or {"combined_value": 1.0})
    monkeypatch.setattr(portfolio, "portfolio_overview", lambda cfg: calls.append("live") or {"combined_value": 2.0})

    result = portfolio.do_get_portfolio_overview(TradingConfig())

    assert calls == ["snapshot"]
    assert result == {"combined_value": 1.0}


def test_do_get_portfolio_overview_reads_live_when_already_taken_today(monkeypatch):
    monkeypatch.setattr(storage, "latest_portfolio_snapshot_date", lambda: date.today())
    calls = []
    monkeypatch.setattr(portfolio, "take_portfolio_snapshot", lambda cfg: calls.append("snapshot") or {"combined_value": 1.0})
    monkeypatch.setattr(portfolio, "portfolio_overview", lambda cfg: calls.append("live") or {"combined_value": 2.0})

    result = portfolio.do_get_portfolio_overview(TradingConfig())

    assert calls == ["live"]
    assert result == {"combined_value": 2.0}


def test_do_get_portfolio_history_unbounded_when_days_is_none(monkeypatch):
    captured = {}

    def _fake_load(since=None):
        captured["since"] = since
        return [(date(2026, 9, 1), 1.0, 0.1, None, 1, 2.0, True, 3.0, None, None, None)]

    monkeypatch.setattr(storage, "load_portfolio_snapshots", _fake_load)

    result = portfolio.do_get_portfolio_history()

    assert captured["since"] is None
    assert result == [{
        "snapshot_date": date(2026, 9, 1), "trading_realized_profit": 1.0, "trading_average_margin": 0.1,
        "trading_daily_profit_volatility": None, "trading_trade_count": 1, "production_stock_value": 2.0,
        "production_stock_targets_configured": True, "combined_value": 3.0, "total_wealth": None,
        "wealth_assets_value": None, "wealth_wallet_balance": None,
    }]


def test_do_get_portfolio_history_with_days_computes_since(monkeypatch):
    captured = {}

    def _fake_load(since=None):
        captured["since"] = since
        return []

    monkeypatch.setattr(storage, "load_portfolio_snapshots", _fake_load)

    portfolio.do_get_portfolio_history(days=7)

    # Inclusive of today: 7 days means today back through 6 days ago.
    from datetime import timedelta
    assert captured["since"] == date.today() - timedelta(days=6)


def test_do_list_manual_item_prices(monkeypatch):
    monkeypatch.setattr(storage, "list_manual_item_prices", lambda: [
        (34, "Tritanium", 5.5, "2026-09-01T00:00:00+00:00"),
    ])
    result = portfolio.do_list_manual_item_prices()
    assert result == {"rows": [
        {"type_id": 34, "type_name": "Tritanium", "price": 5.5, "updated_at": "2026-09-01T00:00:00+00:00"},
    ]}


def test_do_set_manual_item_price_resolves_exact_name(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [(34, "Tritanium")])
    captured = {}
    monkeypatch.setattr(storage, "upsert_manual_item_price",
                        lambda type_id, type_name, price: captured.update(
                            type_id=type_id, type_name=type_name, price=price))

    result = portfolio.do_set_manual_item_price("Tritanium", 5.5)

    assert captured == {"type_id": 34, "type_name": "Tritanium", "price": 5.5}
    assert result == {"type_id": 34, "type_name": "Tritanium", "price": 5.5}


def test_do_set_manual_item_price_rejects_negative_price(monkeypatch):
    from eve_trader.actions import ActionError
    import pytest
    with pytest.raises(ActionError):
        portfolio.do_set_manual_item_price("Tritanium", -1.0)


def test_do_set_manual_item_price_no_match_raises(monkeypatch):
    from eve_trader.actions import ActionError
    import pytest
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [])
    with pytest.raises(ActionError):
        portfolio.do_set_manual_item_price("Nonexistent Item", 1.0)


def test_do_set_manual_item_price_near_match_suggests_it(monkeypatch):
    from eve_trader.actions import ActionError
    import pytest
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [(34, "Tritanium")])
    with pytest.raises(ActionError, match="Tritanium"):
        portfolio.do_set_manual_item_price("Tritanum", 1.0)


def test_do_remove_manual_item_price(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "delete_manual_item_price", lambda type_id: captured.setdefault("type_id", type_id))
    result = portfolio.do_remove_manual_item_price(34)
    assert captured["type_id"] == 34
    assert result == {"removed": 34}

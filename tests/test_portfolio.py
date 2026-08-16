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

"""The snapshot keeps the largest priced types; /wealth-change flags price jumps."""
from __future__ import annotations

from datetime import date, timedelta

from eve_trader import portfolio, storage, timeutil
from eve_trader.config import TradingConfig
from eve_trader.portfolio_wealth import top_types


def _item(type_id, quantity, price, component="assets", owner=1):
    return {
        "component": component, "owner_type": "character", "owner_id": owner,
        "location_id": 60003760, "type_id": type_id, "quantity": quantity,
        "price": price, "manual_only": False,
    }


def test_top_types_sum_per_type_and_skip_unpriced():
    items = [
        _item(34, 100, 5.0, owner=1),
        _item(34, 300, 5.0, owner=2),
        _item(34, 50, 5.0, component="listed"),
        _item(35, 10, 1000.0),
        _item(36, 999, None),
    ]
    rows = top_types(items)
    assert [row["type_id"] for row in rows] == [35, 34]
    trit = rows[1]
    assert trit["quantity"] == 450
    assert trit["value"] == 2250.0
    assert trit["unit_price"] == 5.0
    assert trit["component"] == "assets"


def test_top_types_keeps_twenty():
    rows = top_types([_item(1000 + n, 1, float(n + 1)) for n in range(30)])
    assert len(rows) == 20
    assert rows[0]["type_id"] == 1029


def test_snapshot_stores_the_twenty_largest_types(monkeypatch):
    monkeypatch.setattr(portfolio, "_quotes_available", lambda: True)
    monkeypatch.setattr(
        "eve_trader.trade_realized.report.portfolio_figures",
        lambda: {
            "trading_realized_profit": 0.0, "trading_average_margin": 0.0,
            "trading_daily_profit_volatility": None, "trading_trade_count": 0,
        },
    )
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    collected = {
        "items": [_item(1000 + n, 1, float(n + 1)) for n in range(25)],
        "wallets": [], "escrow": [], "structure_id": None, "escrow_known": True,
        "shares": {kind: ([1], []) for kind in (
            "assets", "blueprints", "wallet_balance", "market_orders", "contracts", "industry_jobs")},
    }
    monkeypatch.setattr(portfolio, "_collect_wealth_cached", lambda cfg=None: collected)
    monkeypatch.setattr(portfolio, "_characters_missing_wallet_scope", lambda ids: [])
    monkeypatch.setattr(portfolio, "_sync_times", lambda shares, known: {})
    upserted = {}
    monkeypatch.setattr(storage, "upsert_portfolio_snapshot",
                        lambda day, values: upserted.update(date=day, **values))

    portfolio.take_portfolio_snapshot(TradingConfig())

    assert upserted["date"] == timeutil.utc_today()
    assert len(upserted["top_positions"]) == 20
    assert upserted["top_positions"][0]["type_id"] == 1024
    assert set(upserted["top_positions"][0]) == {"type_id", "quantity", "unit_price", "value", "component"}


def _row(day: date, total: float, positions):
    values = {col: 0.0 for col in storage.PORTFOLIO_SNAPSHOT_COLUMNS}
    values["trading_daily_profit_volatility"] = None
    values["trading_trade_count"] = 0
    values["production_stock_targets_configured"] = False
    values["total_wealth"] = total
    values["top_positions"] = positions
    return (day,) + tuple(values[col] for col in storage.PORTFOLIO_SNAPSHOT_COLUMNS)


def _position(type_id, quantity, price, component="assets"):
    return {"type_id": type_id, "quantity": quantity, "unit_price": price,
            "value": quantity * price, "component": component}


def _change(monkeypatch, today_positions, yesterday_positions, total=1_000_000.0):
    today = date(2026, 11, 10)
    rows = [
        _row(today, total, today_positions),
        _row(today - timedelta(days=1), total, yesterday_positions),
    ]
    monkeypatch.setattr(storage, "load_portfolio_snapshots", lambda since=None: rows)
    monkeypatch.setattr(storage, "sde_type_names", lambda ids: {int(i): f"Type {i}" for i in ids})
    return portfolio.wealth_change(today)


def test_doubled_price_of_a_top_type_is_a_price_jump(monkeypatch):
    change = _change(monkeypatch, [_position(34, 1000, 20.0)], [_position(34, 1000, 10.0)])
    [jump] = change["price_jumps"]
    assert jump["type_id"] == 34
    assert jump["type_name"] == "Type 34"
    assert jump["previous_price"] == 10.0
    assert jump["price"] == 20.0
    assert jump["value_change"] == 10_000.0


def test_quantity_change_alone_is_not_a_price_jump(monkeypatch):
    change = _change(monkeypatch, [_position(34, 5000, 10.0)], [_position(34, 1000, 10.0)])
    assert change["price_jumps"] == []


def test_small_value_move_or_small_price_move_is_not_a_jump(monkeypatch):
    # Doubled, but 100 ISK of value is far below 1 % of the total.
    assert _change(monkeypatch, [_position(34, 10, 20.0)], [_position(34, 10, 10.0)])["price_jumps"] == []
    # Large value, price up only 50 %.
    assert _change(monkeypatch, [_position(34, 1_000_000, 15.0)],
                   [_position(34, 1_000_000, 10.0)])["price_jumps"] == []


def test_halved_price_counts_and_missing_lists_give_no_jumps(monkeypatch):
    change = _change(monkeypatch, [_position(34, 10_000, 5.0)], [_position(34, 10_000, 10.0)])
    assert [j["type_id"] for j in change["price_jumps"]] == [34]
    assert _change(monkeypatch, [_position(34, 1000, 5.0)], None)["price_jumps"] == []

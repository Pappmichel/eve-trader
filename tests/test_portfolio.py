from dataclasses import dataclass
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


def test_take_portfolio_snapshot_upserts_today_with_no_wealth_sharing(monkeypatch):
    # No owner shares anything with "portfolio" - total_wealth degrades to
    # 0.0 (not None), matching total_wealth()'s own "nobody shares" shape.
    monkeypatch.setattr(storage, "read_table", lambda table: _trades_df([]))
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    _stub_sharing(monkeypatch)
    monkeypatch.setattr(storage, "load_all_assets", lambda char_ids, corp_ids: [])
    monkeypatch.setattr(storage, "load_owned_blueprints", lambda char_ids, corp_ids: [])
    monkeypatch.setattr(storage, "sum_wallet_balances", lambda char_ids, corp_ids: 0.0)
    upserted = {}
    monkeypatch.setattr(storage, "upsert_portfolio_snapshot",
                        lambda snapshot_date, values: upserted.update(date=snapshot_date, **values))

    result = portfolio.take_portfolio_snapshot(TradingConfig())

    assert upserted["date"] == date.today()
    assert upserted["total_wealth"] == 0.0
    assert upserted["wealth_assets_value"] == 0.0
    assert upserted["wealth_wallet_balance"] == 0.0
    assert upserted["combined_value"] == 0.0
    assert result["total_wealth"] == 0.0
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


# ------------------------------------------------------------ total_wealth
def _current_price(type_id, sell):
    from eve_trader.goonmetrics_client import CurrentPrice
    return CurrentPrice(type_id=type_id, updated="", buy=0.0, sell=sell)


@dataclass
class _FakeTokenRecord:
    character_id: int
    character_name: str
    scopes: str


class _FakeTokenManager:
    def __init__(self, records):
        self._records = records

    def __call__(self, *_args, **_kwargs):
        return self

    def list_records(self):
        return self._records


def _stub_sharing(monkeypatch, **owner_ids):
    """owner_ids keys like 'assets_character', 'assets_corporation', ...
    default []."""
    from eve_trader.esi_data import access as esi_access

    def _fake_shared_owner_ids(data_kind, tool_key, owner_type):
        return owner_ids.get(f"{data_kind}_{owner_type}", [])

    monkeypatch.setattr(esi_access, "shared_owner_ids", _fake_shared_owner_ids)


def test_priced_prefers_manual_over_home_and_jita(monkeypatch):
    from eve_trader.production.config import PRODUCTION_CONFIG
    from eve_trader.production import pricing as production_pricing
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_market", "insmother")
    monkeypatch.setattr(storage, "load_manual_item_prices", lambda: {34: 999.0})
    monkeypatch.setattr(production_pricing, "_goonmetrics_prices",
                        lambda market, ids: {tid: _current_price(tid, 10.0) for tid in ids})

    prices = portfolio._priced({34, 35})

    assert prices[34] == 999.0  # manual override wins
    assert prices[35] == 10.0


def test_priced_falls_back_to_jita_when_home_has_no_quote(monkeypatch):
    from eve_trader.production.config import PRODUCTION_CONFIG
    from eve_trader.production import pricing as production_pricing
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_market", "insmother")
    monkeypatch.setattr(storage, "load_manual_item_prices", lambda: {})

    def _fake_prices(market, ids):
        if market == "insmother":
            return {}
        return {tid: _current_price(tid, 20.0) for tid in ids}

    monkeypatch.setattr(production_pricing, "_goonmetrics_prices", _fake_prices)

    prices = portfolio._priced({34})
    assert prices[34] == 20.0


def test_priced_excludes_type_with_no_quote_anywhere(monkeypatch):
    from eve_trader.production.config import PRODUCTION_CONFIG
    from eve_trader.production import pricing as production_pricing
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_market", None)
    monkeypatch.setattr(storage, "load_manual_item_prices", lambda: {})
    monkeypatch.setattr(production_pricing, "_goonmetrics_prices", lambda market, ids: {})

    assert portfolio._priced({34}) == {}


def test_value_and_gaps_excludes_unpriced_not_zeroed():
    rows = [(34, 10), (35, 5)]
    value, priced, unpriced = portfolio._value_and_gaps(rows, {34: 2.0})
    assert value == 20.0
    assert priced == 1
    assert unpriced == 1


def test_characters_missing_wallet_scope_empty_for_no_owners():
    assert portfolio._characters_missing_wallet_scope(set()) == []


def test_characters_missing_wallet_scope_flags_character_without_scope(monkeypatch):
    import eve_trader.auth as auth_module
    monkeypatch.setattr(auth_module, "TokenManager", _FakeTokenManager([
        _FakeTokenRecord(character_id=1, character_name="Alice", scopes="esi-assets.read_assets.v1"),
        _FakeTokenRecord(character_id=2, character_name="Bob", scopes="esi-wallet.read_character_wallet.v1"),
    ]))

    result = portfolio._characters_missing_wallet_scope({1, 2})

    assert result == [{"character_id": 1, "character_name": "Alice"}]


def test_characters_missing_wallet_scope_uses_id_when_name_unknown(monkeypatch):
    import eve_trader.auth as auth_module
    monkeypatch.setattr(auth_module, "TokenManager", _FakeTokenManager([]))
    result = portfolio._characters_missing_wallet_scope({7})
    assert result == [{"character_id": 7, "character_name": "7"}]


def test_total_wealth_scoped_to_portfolio_sharing_only(monkeypatch):
    _stub_sharing(monkeypatch, assets_character=[1], wallet_balance_character=[1])
    monkeypatch.setattr(storage, "load_all_assets", lambda char_ids, corp_ids: [(34, 100)] if char_ids == [1] else [])
    monkeypatch.setattr(storage, "load_owned_blueprints", lambda char_ids, corp_ids: [])
    monkeypatch.setattr(storage, "sum_wallet_balances", lambda char_ids, corp_ids: 500.0 if char_ids == [1] else 0.0)
    monkeypatch.setattr(portfolio, "_priced", lambda type_ids: {34: 5.0})
    import eve_trader.auth as auth_module
    monkeypatch.setattr(auth_module, "TokenManager", _FakeTokenManager([
        _FakeTokenRecord(character_id=1, character_name="Alice", scopes="esi-wallet.read_character_wallet.v1"),
    ]))

    result = portfolio.total_wealth(TradingConfig())

    assert result["wealth_assets_value"] == 500.0  # 100 * 5.0
    assert result["wealth_blueprints_value"] == 0.0
    assert result["wealth_wallet_balance"] == 500.0
    assert result["total_wealth"] == 1000.0
    assert result["wealth_priced_items"] == 1
    assert result["wealth_unpriced_items"] == 0
    assert result["characters_missing_wallet_scope"] == []


def test_total_wealth_a_character_shared_only_with_production_is_excluded(monkeypatch):
    # A character shared with "production" only must NOT appear in
    # Portfolio's Total Wealth - sharing is per-tool, never bypassed.
    _stub_sharing(monkeypatch)  # nothing shared with "portfolio"
    monkeypatch.setattr(storage, "load_all_assets", lambda char_ids, corp_ids: [])
    monkeypatch.setattr(storage, "load_owned_blueprints", lambda char_ids, corp_ids: [])
    monkeypatch.setattr(storage, "sum_wallet_balances", lambda char_ids, corp_ids: 0.0)

    result = portfolio.total_wealth(TradingConfig())

    assert result["total_wealth"] == 0.0
    assert result["characters_missing_wallet_scope"] == []


def test_take_portfolio_snapshot_includes_total_wealth(monkeypatch):
    monkeypatch.setattr(storage, "read_table", lambda table: _trades_df([]))
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    monkeypatch.setattr(portfolio, "total_wealth", lambda cfg: {
        "total_wealth": 42.0, "wealth_assets_value": 42.0, "wealth_blueprints_value": 0.0,
        "wealth_wallet_balance": 0.0, "wealth_priced_items": 1, "wealth_unpriced_items": 0,
        "characters_missing_wallet_scope": [],
    })
    upserted = {}
    monkeypatch.setattr(storage, "upsert_portfolio_snapshot",
                        lambda snapshot_date, values: upserted.update(values))

    result = portfolio.take_portfolio_snapshot(TradingConfig())

    assert upserted["total_wealth"] == 42.0
    assert upserted["wealth_assets_value"] == 42.0
    assert result["total_wealth"] == 42.0

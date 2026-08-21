import datetime as dt

import pandas as pd

from eve_trader import trade_reconciliation
from eve_trader.config import TradingConfig
from eve_trader.trade_reconciliation import average_daily_sold_by_type, reconcile_realized_trades

JITA_4_4_STATION_ID = 60003760  # real EVE station ID, used as the test's "Jita" location


class FakeClient:
    """Minimal ESIClient double - only character_wallet_transactions is used
    by reconcile_realized_trades/fetch_recent_transactions. Single-page fixture
    data (a real call chain with from_id pagination is covered separately in
    test_fetch_recent_transactions_pages.py) - always returns the full fixture
    on the first (from_id=None) call and nothing on any follow-up call, same
    as a real character with fewer than 2500 total transactions."""
    def __init__(self, buyer_txns, seller_txns):
        self._by_char = {1: buyer_txns, 2: seller_txns}

    def character_wallet_transactions(self, character_id, auth_role, from_id=None):
        return self._by_char[character_id] if from_id is None else []


def _iso(days_ago: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


def test_reconcile_includes_buy_broker_fee(monkeypatch):
    # structure_sell_haircut=1.0 and item_volumes=0 isolate the buy-side fee's
    # effect from the (already-tested) sell haircut/freight math.
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(import_cost_per_m3=900.0, jita_buy_broker_fee=0.0147,
                         structure_sell_haircut=1.0, lookback_days=30)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    client = FakeClient(buys, sells)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    # landed = 1000 * 1.0147 + 0 (volume) * 900 = 1014.7 ; net_sell = 1200 * 1.0
    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 2) == round(1200.0 - 1014.7, 2)


def test_reconcile_ignores_buys_outside_the_forge(monkeypatch):
    # Confirmed real bug: unlike the sell side (already scoped to
    # cfg.structure_id), the buy side had no location filter at all - a
    # wallet transaction anywhere, not just The Forge, could enter the FIFO
    # match. A buy at some unrelated out-of-region station must not get
    # matched against a structure sale.
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30)
    other_region_station_id = 60011866  # some out-of-region NPC station
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": other_region_station_id, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    client = FakeClient(buys, sells)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert trades == []


def test_average_daily_sold_by_type_empty_table_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(trade_reconciliation.storage, "read_table", lambda table: pd.DataFrame())
    assert average_daily_sold_by_type(TradingConfig(lookback_days=30)) == {}


def test_average_daily_sold_by_type_sums_matched_qty_over_lookback_days(monkeypatch):
    # GitHub issue #51: this - not sell_volume/order-book depth - is what
    # "Profit / Day" is computed from. Two matched-sell rows for the same
    # type_id (a sell split across two FIFO buy-lot matches) must sum, not
    # overwrite each other.
    df = pd.DataFrame([
        {"run_ts": "2026-08-20T00:00:00", "type_id": 100, "matched_qty": 40},
        {"run_ts": "2026-08-20T00:00:00", "type_id": 100, "matched_qty": 20},
        {"run_ts": "2026-08-20T00:00:00", "type_id": 200, "matched_qty": 10},
    ])
    monkeypatch.setattr(trade_reconciliation.storage, "read_table", lambda table: df)

    result = average_daily_sold_by_type(TradingConfig(lookback_days=30))

    assert result == {100: 2.0, 200: 10.0 / 30}


def test_average_daily_sold_by_type_only_considers_the_latest_run(monkeypatch):
    # save_realized_trades wholesale-replaces the table every run, so this is
    # mostly defensive - but a stale second run_ts must not be double-counted.
    df = pd.DataFrame([
        {"run_ts": "2026-08-01T00:00:00", "type_id": 100, "matched_qty": 999},
        {"run_ts": "2026-08-20T00:00:00", "type_id": 100, "matched_qty": 30},
    ])
    monkeypatch.setattr(trade_reconciliation.storage, "read_table", lambda table: df)

    result = average_daily_sold_by_type(TradingConfig(lookback_days=30))

    assert result == {100: 1.0}


def test_average_daily_sold_by_type_guards_against_zero_lookback_days(monkeypatch):
    df = pd.DataFrame([{"run_ts": "2026-08-20T00:00:00", "type_id": 100, "matched_qty": 30}])
    monkeypatch.setattr(trade_reconciliation.storage, "read_table", lambda table: df)

    assert average_daily_sold_by_type(TradingConfig(lookback_days=0)) == {}


def test_reconcile_accepts_buys_anywhere_in_the_forge_not_just_jita_itself(monkeypatch):
    # Confirmed with the user: any station in The Forge region counts, not
    # just Jita's own solar system - a trader can legitimately buy from
    # other Forge stations too.
    other_forge_station_id = 60004588  # some other station in The Forge, not Jita
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID, other_forge_station_id}))
    cfg = TradingConfig(lookback_days=30)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": other_forge_station_id, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    client = FakeClient(buys, sells)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1

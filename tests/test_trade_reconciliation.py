import datetime as dt

import pandas as pd

from eve_trader import trade_reconciliation
from eve_trader.config import TradingConfig, WALLET_DIVISION_IDS
from eve_trader.esi_client import ESIError
from eve_trader.trade_reconciliation import average_daily_sold_by_type, reconcile_realized_trades

JITA_4_4_STATION_ID = 60003760  # real EVE station ID, used as the test's "Jita" location


class FakeClient:
    """Minimal ESIClient double - character_wallet_transactions/
    character_wallet_journal are used by reconcile_realized_trades, plus
    character_public_info / corporation_wallet_* for the corp-wallet merge.
    Single-page fixture data (a real call chain with from_id pagination is
    covered separately in test_wallet_transaction_pagination.py) - always
    returns the full fixture on the first (from_id=None) call and nothing on
    any follow-up call, same as a real character with fewer than 2500 total
    transactions. `journal_entries` defaults to {} (empty per character) -
    every existing test exercises the fully-modeled fallback path unless it
    explicitly opts into PB-03's real-tax path by passing journal data.

    Corp methods default to "this character has no corporation_id", so
    existing character-only tests never touch corp wallets. Pass
    `character_corps` / `corp_txns` / `corp_journal` to opt in; set
    `corp_error=ESIError(...)` to simulate a missing Accountant role.
    """
    def __init__(self, buyer_txns, seller_txns, journal_entries=None,
                 character_corps=None, corp_txns=None, corp_journal=None,
                 corp_error=None):
        self._by_char = {1: buyer_txns, 2: seller_txns}
        self._journal_by_char = journal_entries or {}
        self._character_corps = character_corps or {}
        # (corporation_id, division) -> list[txn]
        self._corp_txns = corp_txns or {}
        # (corporation_id, division) -> list[journal entry]
        self._corp_journal = corp_journal or {}
        self._corp_error = corp_error
        self.corp_txn_calls: list[tuple] = []
        self.character_txn_by_id: dict[int, list] = dict(self._by_char)

    def character_wallet_transactions(self, character_id, auth_role, from_id=None):
        txns = self.character_txn_by_id.get(character_id, self._by_char.get(character_id, []))
        return txns if from_id is None else []

    def character_wallet_journal(self, character_id, auth_role):
        return self._journal_by_char.get(character_id, [])

    def character_public_info(self, character_id):
        corp_id = self._character_corps.get(character_id)
        return {"corporation_id": corp_id} if corp_id else {}

    def corporation_wallet_transactions(self, corporation_id, division, auth_role, from_id=None):
        self.corp_txn_calls.append((corporation_id, division, auth_role, from_id))
        if self._corp_error is not None:
            raise self._corp_error
        if from_id is not None:
            return []
        return list(self._corp_txns.get((corporation_id, division), []))

    def corporation_wallet_journal(self, corporation_id, division, auth_role):
        if self._corp_error is not None:
            raise self._corp_error
        return list(self._corp_journal.get((corporation_id, division), []))


def _iso(days_ago: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


def _mt_and_tax_entries(mt_id: int, transaction_id: int, gross: float, tax_rate: float, date: str) -> list[dict]:
    """A real (market_transaction, transaction_tax) journal-entry pair, in
    the exact shape T1-01's live verification (2026-09-25,
    evetrader.duckdns.org) found: linked to the wallet transaction via
    context_id/context_id_type (NOT journal_ref_id, confirmed live to never
    match any real journal entry), with the tax entry immediately adjacent
    (id + 1), same ref_type/date - checked against all 2447 real structure
    sells in that live check, not just sampled."""
    tax = gross * tax_rate
    return [
        {"id": mt_id, "ref_type": "market_transaction", "amount": gross, "date": date,
         "context_id": transaction_id, "context_id_type": "market_transaction_id"},
        {"id": mt_id + 1, "ref_type": "transaction_tax", "amount": -tax, "date": date},
    ]


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


def test_reconcile_never_matches_a_sell_against_a_later_buy(monkeypatch):
    """PB-02 regression (business-logic audit, 2026-08-29): a sale can't be
    funded by inventory bought after it sold - FIFO used to only order buys
    chronologically among themselves, never checking a matched buy actually
    predates its sell. Live evidence before this fix: 324 of 1640
    realized_trades rows (20%) had buy_date > sell_date, accounting for
    21.8% of the reported net realized profit."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(1), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]  # more recent than the sell below
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(2), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    client = FakeClient(buys, sells)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert trades == []  # the only available buy postdates the sell - must not fabricate a cost basis


def test_reconcile_lets_a_later_sell_reach_a_buy_deferred_by_an_earlier_sell(monkeypatch):
    """The buy_date > sell_date check must `break` out of the inner match
    loop, not advance past the too-late buy entirely - a buy that's too late
    for one (earlier) sell can still be the valid cost basis for a later
    sell whose own date comes after it."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30)
    later_sell_date = _iso(1)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [
        {"is_buy": False, "type_id": 100, "date": _iso(3), "unit_price": 1200.0, "quantity": 10,
         "location_id": cfg.structure_id, "transaction_id": 2},  # older than the buy - must not match
        {"is_buy": False, "type_id": 100, "date": later_sell_date, "unit_price": 1300.0, "quantity": 10,
         "location_id": cfg.structure_id, "transaction_id": 3},  # newer than the buy - must match
    ]
    client = FakeClient(buys, sells)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    assert trades[0].sell_date == later_sell_date
    assert trades[0].matched_qty == 10


def test_reconcile_matches_a_buy_older_than_the_sell_side_lookback_window(monkeypatch):
    """PB-05 (business-logic audit, 2026-08-29): buys are fetched over a
    longer window than sells (_BUY_LOOKBACK_MULTIPLIER) specifically so a
    sell inside cfg.lookback_days can still be matched to real, older
    inventory. Without this, PB-02's date-ordering fix (a sell can't be
    funded by a later buy) would silently drop such a sell instead of
    fabricating a wrong cost basis for it - safe, but an avoidable
    under-report of real realized profit."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30)  # buy window becomes 90 days (_BUY_LOOKBACK_MULTIPLIER=3)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(60), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]  # older than the 30-day sell window
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    client = FakeClient(buys, sells)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    assert trades[0].matched_qty == 10


def test_reconcile_still_ignores_a_buy_beyond_even_the_widened_buy_window(monkeypatch):
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30)  # buy window becomes 90 days
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(120), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]  # older than even the 90-day buy window
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    client = FakeClient(buys, sells)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert trades == []


def test_reconcile_uses_real_tax_and_broker_fee_from_wallet_journal_when_available(monkeypatch):
    """T1-01 (2026-09-25), live-verified against evetrader.duckdns.org: a
    journal-matched sell's real net proceeds are (market_transaction gross
    amount - the adjacent transaction_tax entry's real amount), with only
    cfg.structure_broker_fee (modeled - broker's fee is per-ORDER, not
    per-fill) deducted on top - never cfg.structure_sell_haircut, which is
    the no-journal-match fallback only."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_broker_fee=0.015, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    journal_entries = {2: _mt_and_tax_entries(mt_id=100, transaction_id=2, gross=11000.0,
                                               tax_rate=0.03375, date=_iso(1))}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    real_tax = 11000.0 * 0.03375
    expected_net_sell = ((11000.0 - real_tax) / 10) * (1 - cfg.structure_broker_fee)
    expected_landed = 1000.0
    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 6) == round(expected_net_sell - expected_landed, 6)


def test_reconcile_does_not_reintroduce_t1_01_missing_tax_deduction(monkeypatch):
    """Regression guard for T1-01 (2026-09-25): the original bug treated the
    journal amount as already net of tax and multiplied it by only the
    SCC+broker-only portion of the haircut - never deducting any real tax
    at all for a journal-matched sell. This must never come back: profit
    must be strictly lower than that old formula, AND strictly higher than
    the fully-modeled fallback (which still bakes in a phantom "SCC
    surcharge" that T1-01 confirmed does not apply to market sells at all -
    see structure_sell_haircut's own config.py comment) - i.e. the new
    formula must land strictly between the two old (wrong, in opposite
    directions) figures, not coincide with either."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=0.9463,
                         structure_broker_fee=0.015, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    journal_entries = {2: _mt_and_tax_entries(mt_id=100, transaction_id=2, gross=12000.0,
                                               tax_rate=0.03375, date=_iso(1))}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty

    # The ORIGINAL bug's formula: journal amount x (haircut + assumed 3.37% tax) - never deducted real tax.
    _original_bug_assumed_tax_rate = 0.0337
    old_buggy_net_sell = (12000.0 / 10) * (cfg.structure_sell_haircut + _original_bug_assumed_tax_rate)
    old_buggy_profit_per_unit = old_buggy_net_sell - 1000.0
    assert profit_per_unit < old_buggy_profit_per_unit, (
        "journal-matched sale profit must be lower than the original bug's "
        "formula - that formula never deducted sales tax at all"
    )

    # The Phase-1-interim fallback-equivalent formula (still includes the
    # since-disproven "SCC surcharge" baked into structure_sell_haircut).
    interim_net_sell = (12000.0 / 10) * cfg.structure_sell_haircut
    interim_profit_per_unit = interim_net_sell - 1000.0
    assert profit_per_unit > interim_profit_per_unit, (
        "using the real per-sale tax (3.375%) plus only the confirmed-real "
        "broker's fee (1.5%) must yield a higher profit than a formula that "
        "still deducts a phantom SCC-surcharge-like amount market sells "
        "never actually incur"
    )

    real_tax = 12000.0 * 0.03375
    expected_net_sell = ((12000.0 - real_tax) / 10) * (1 - cfg.structure_broker_fee)
    assert round(profit_per_unit, 6) == round(expected_net_sell - 1000.0, 6)


def test_reconcile_journal_matched_sale_with_zero_effective_tax(monkeypatch):
    """T1-01: a real transaction_tax entry whose amount is 0 (structure_
    broker_fee also 0) must not have the real-tax branch invent a
    deduction - it must return the exact unreduced gross-per-unit figure,
    same as the fallback would with structure_sell_haircut=1.0. This is
    distinct from "no tax entry found at all" (falls back safely instead -
    see test_reconcile_falls_back_to_modeled_haircut_when_no_journal_entry_matches)."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0,
                         structure_broker_fee=0.0, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    journal_entries = {2: _mt_and_tax_entries(mt_id=100, transaction_id=2, gross=12000.0,
                                               tax_rate=0.0, date=_iso(1))}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 6) == round((12000.0 / 10) - 1000.0, 6)


def test_reconcile_multiple_journal_matched_sales_do_not_cross_contaminate(monkeypatch):
    """Two independent journal-matched sells of the same type_id must each
    resolve their OWN (market_transaction, transaction_tax) pair via their
    own transaction_id - a regression guard against the id+1 adjacency
    lookup accidentally pairing one sale's market_transaction with a
    DIFFERENT sale's tax entry, or dropping the deduction for one of them."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_broker_fee=0.015, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(5), "unit_price": 1000.0, "quantity": 20,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [
        {"is_buy": False, "type_id": 100, "date": _iso(2), "unit_price": 1200.0, "quantity": 10,
         "location_id": cfg.structure_id, "transaction_id": 2},
        {"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1300.0, "quantity": 10,
         "location_id": cfg.structure_id, "transaction_id": 3},
    ]
    journal_entries = {2: (
        _mt_and_tax_entries(mt_id=100, transaction_id=2, gross=12000.0, tax_rate=0.03375, date=_iso(2))
        + _mt_and_tax_entries(mt_id=200, transaction_id=3, gross=13000.0, tax_rate=0.03375, date=_iso(1))
    )}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 2
    by_gross = {12000.0: trades[0], 13000.0: trades[1]}
    for gross, trade in by_gross.items():
        profit_per_unit = trade.realized_profit / trade.matched_qty
        real_tax = gross * 0.03375
        expected = ((gross - real_tax) / 10) * (1 - cfg.structure_broker_fee) - 1000.0
        assert round(profit_per_unit, 6) == round(expected, 6)
    total_profit = sum(t.realized_profit for t in trades)
    expected_total = sum(
        (((gross - gross * 0.03375) / 10) * (1 - cfg.structure_broker_fee) - 1000.0) * 10
        for gross in (12000.0, 13000.0)
    )
    assert round(total_profit, 6) == round(expected_total, 6)


def test_reconcile_journal_matched_sale_rounding(monkeypatch):
    """Non-round journal amounts/rates must not be truncated or rounded
    inside reconcile_realized_trades itself - full float precision is
    preserved through to realized_profit, matching direct recomputation of
    the same formula in the same operation order (any UI-side rounding is
    display-only, out of scope here)."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_broker_fee=0.015, jita_buy_broker_fee=0.0147)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 333.33, "quantity": 3,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 456.78, "quantity": 3,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    gross = 1370.33
    tax_rate = 0.033750
    journal_entries = {2: _mt_and_tax_entries(mt_id=100, transaction_id=2, gross=gross,
                                               tax_rate=tax_rate, date=_iso(1))}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    real_tax = gross * tax_rate
    expected_real_net_sell = (gross - real_tax) / 3
    expected_net_sell = expected_real_net_sell * (1 - cfg.structure_broker_fee)
    expected_landed = 333.33 * (1 + cfg.jita_buy_broker_fee)
    expected_profit_per_unit = expected_net_sell - expected_landed
    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert profit_per_unit == expected_profit_per_unit  # exact float match, no internal rounding


def test_reconcile_journal_matched_sale_preserves_buy_side_fee_and_freight_semantics(monkeypatch):
    """T1-01 only touches the sell side's net_sell formula - buy-side
    broker's fee and freight (import_cost_per_m3) must be entirely
    unaffected for a journal-matched sale."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_broker_fee=0.015,
                         jita_buy_broker_fee=0.0147, import_cost_per_m3=900.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    journal_entries = {2: _mt_and_tax_entries(mt_id=100, transaction_id=2, gross=12000.0,
                                               tax_rate=0.03375, date=_iso(1))}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 5.0}, cfg=cfg,
    )

    assert len(trades) == 1
    real_tax = 12000.0 * 0.03375
    expected_net_sell = ((12000.0 - real_tax) / 10) * (1 - cfg.structure_broker_fee)
    expected_freight = 5.0 * cfg.import_cost_per_m3
    expected_landed = 1000.0 * (1 + cfg.jita_buy_broker_fee) + expected_freight
    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 6) == round(expected_net_sell - expected_landed, 6)


def test_reconcile_falls_back_to_modeled_haircut_when_no_journal_entry_matches(monkeypatch):
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2, "journal_ref_id": 999}]  # no matching journal entry
    client = FakeClient(buys, sells, journal_entries={2: []})

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 2) == round(1200.0 - 1000.0, 2)  # fully modeled: haircut=1.0, broker_fee=0.0


def test_reconcile_ignores_journal_ref_id_and_uses_context_id_linkage(monkeypatch):
    """T1-01's live verification (2026-09-25) found the wallet transaction's
    own journal_ref_id NEVER equals any real journal entry's id (confirmed
    0/2447 on live production data) - a decoy entry sitting at that id must
    be ignored, and the real market_transaction/transaction_tax pair must
    still be found via context_id/context_id_type instead."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_broker_fee=0.015, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    # journal_ref_id=555 is a decoy pointing at nothing real - present only
    # because real ESI data always has this field, must be entirely inert.
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2, "journal_ref_id": 555}]
    journal_entries = {2: _mt_and_tax_entries(mt_id=100, transaction_id=2, gross=12000.0,
                                               tax_rate=0.03375, date=_iso(1))}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    real_tax = 12000.0 * 0.03375
    expected_net_sell = ((12000.0 - real_tax) / 10) * (1 - cfg.structure_broker_fee)
    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 6) == round(expected_net_sell - 1000.0, 6)


def test_reconcile_ignores_non_market_transaction_journal_entries(monkeypatch):
    """An entry that happens to carry the right context_id/context_id_type
    but the WRONG ref_type (e.g. a brokers_fee entry) must never be treated
    as the sell's own market_transaction entry."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    journal_entries = {2: [{"id": 555, "ref_type": "brokers_fee", "amount": -50.0, "date": _iso(1),
                            "context_id": 2, "context_id_type": "market_transaction_id"}]}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 2) == round(1200.0 - 1000.0, 2)  # fell back to modeled, ignored the brokers_fee entry


def test_reconcile_market_transaction_found_but_tax_entry_missing_falls_back(monkeypatch):
    """A market_transaction entry with no verified adjacent transaction_tax
    (missing, wrong ref_type, or mismatched date) must fall back to the
    modeled formula rather than guess at a tax figure - the ~2% real-world
    edge case T1-01's live check found at the fetched journal window's
    boundary (51 of 2447 real sells)."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    # market_transaction present and correctly linked, but no id+1 sibling at all.
    journal_entries = {2: [{"id": 100, "ref_type": "market_transaction", "amount": 12000.0, "date": _iso(1),
                            "context_id": 2, "context_id_type": "market_transaction_id"}]}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 2) == round(1200.0 - 1000.0, 2)  # fully modeled fallback


def test_reconcile_rejects_implausible_tax_from_an_interleaved_same_second_sale(monkeypatch):
    """T1-01 follow-up (independent challenge pass, 2026-09-26): a real,
    demonstrated failure - two sells in the same second whose journal
    entries interleave as (MT_A, MT_B, TAX_A, TAX_B) instead of the usual
    (MT_A, TAX_A, MT_B, TAX_B) make the SMALL sale's id+1 land on the LARGE
    sale's tax entry - same date, right ref_type, wrong sale. Before the
    plausibility guard, this produced a wildly wrong (deeply negative)
    profit for the small sale. It must now fall back to the modeled
    formula instead of trusting an implausible tax/gross ratio."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0,
                         structure_broker_fee=0.0, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1.0, "quantity": 10000000,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1},
            {"is_buy": True, "type_id": 200, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 2}]
    same_instant = _iso(1)
    sells = [
        {"is_buy": False, "type_id": 100, "date": same_instant, "unit_price": 110.0, "quantity": 10000000,
         "location_id": cfg.structure_id, "transaction_id": 10},  # the LARGE sale
        {"is_buy": False, "type_id": 200, "date": same_instant, "unit_price": 120.0, "quantity": 10,
         "location_id": cfg.structure_id, "transaction_id": 11},  # the SMALL sale
    ]
    # Interleaved: MT_A(500)=large sale, MT_B(501)=small sale, TAX_A(502), TAX_B(503).
    journal_entries = {2: [
        {"id": 500, "ref_type": "market_transaction", "amount": 1_100_000_000.0, "date": same_instant,
         "context_id": 10, "context_id_type": "market_transaction_id"},
        {"id": 501, "ref_type": "market_transaction", "amount": 1200.0, "date": same_instant,
         "context_id": 11, "context_id_type": "market_transaction_id"},
        {"id": 502, "ref_type": "transaction_tax", "amount": -37_125_000.0, "date": same_instant},  # A's real tax
        {"id": 503, "ref_type": "transaction_tax", "amount": -40.5, "date": same_instant},  # B's real tax
    ]}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget", 200: "Gadget"}, item_volumes={100: 0.0, 200: 0.0}, cfg=cfg,
    )

    small_sale = next(t for t in trades if t.type_id == 200)
    profit_per_unit = small_sale.realized_profit / small_sale.matched_qty
    # Must fall back to the fully modeled formula (haircut=1.0, no broker
    # fee) rather than inherit the large sale's ~3.1M% "tax rate".
    assert round(profit_per_unit, 2) == round(120.0 - 1000.0, 2)
    assert profit_per_unit > -1000  # sanity: nowhere near the pre-fix wrong-pairing magnitude


def test_reconcile_ignores_journal_entries_outside_the_lookback_window(monkeypatch):
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    # Real journal entries exist but are far outside the lookback window - must not be used.
    journal_entries = {2: _mt_and_tax_entries(mt_id=100, transaction_id=2, gross=11000.0,
                                               tax_rate=0.03375, date=_iso(400))}
    client = FakeClient(buys, sells, journal_entries=journal_entries)

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 2) == round(1200.0 - 1000.0, 2)  # journal entries too old, fell back to modeled


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


def test_reconcile_matches_corp_funded_fill_absent_from_character_wallet(monkeypatch):
    """The Phase 8 bug: a corp-wallet sell never appears in the placing
    character's personal wallet, so character-only reconcile dropped it."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)
    corp_buy = {"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
                "location_id": JITA_4_4_STATION_ID, "transaction_id": 9001, "journal_ref_id": 1}
    corp_sell = {"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
                 "location_id": cfg.structure_id, "transaction_id": 9002, "journal_ref_id": 2}
    client = FakeClient(
        buyer_txns=[], seller_txns=[],
        character_corps={1: 99, 2: 99},
        corp_txns={(99, 1): [corp_buy, corp_sell]},
    )

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    assert trades[0].matched_qty == 10
    assert round(trades[0].realized_profit, 2) == round((1200.0 - 1000.0) * 10, 2)


def test_reconcile_mixed_character_and_corp_period_does_not_double_count(monkeypatch):
    """A mixed window: personal fill and corp fill of different types both
    match, and a shared-corp buyer+seller pair fetches that corp once so the
    corp fill is not counted twice."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)
    char_buy = {"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
                "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}
    char_sell = {"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
                 "location_id": cfg.structure_id, "transaction_id": 2}
    corp_buy = {"is_buy": True, "type_id": 200, "date": _iso(2), "unit_price": 500.0, "quantity": 4,
                "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}
    corp_sell = {"is_buy": False, "type_id": 200, "date": _iso(1), "unit_price": 800.0, "quantity": 4,
                 "location_id": cfg.structure_id, "transaction_id": 2}
    client = FakeClient(
        buyer_txns=[char_buy], seller_txns=[char_sell],
        character_corps={1: 99, 2: 99},
        corp_txns={(99, 1): [corp_buy, corp_sell]},
    )

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget", 200: "Gadget"},
        item_volumes={100: 0.0, 200: 0.0}, cfg=cfg,
    )

    by_type = {t.type_id: t for t in trades}
    assert set(by_type) == {100, 200}
    assert by_type[100].matched_qty == 10
    assert by_type[200].matched_qty == 4
    assert {call[0] for call in client.corp_txn_calls} == {99}
    assert {call[2] for call in client.corp_txn_calls} == {"buyer"}
    assert len([c for c in client.corp_txn_calls if c[3] is None]) == len(WALLET_DIVISION_IDS)


def test_reconcile_skips_corp_without_accountant_and_keeps_character_fills(monkeypatch):
    """Missing Accountant/Junior_Accountant (or the unwired corp-wallets
    scope) must skip that corp non-fatally — character matching still runs."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)
    buys = [{"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
             "location_id": JITA_4_4_STATION_ID, "transaction_id": 1}]
    sells = [{"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
              "location_id": cfg.structure_id, "transaction_id": 2}]
    client = FakeClient(
        buys, sells,
        character_corps={1: 99, 2: 99},
        corp_txns={(99, 1): [
            {"is_buy": False, "type_id": 200, "date": _iso(1), "unit_price": 9999.0, "quantity": 50,
             "location_id": cfg.structure_id, "transaction_id": 9002},
        ]},
        corp_error=ESIError("HTTP 403: character does not have the required role"),
    )

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget", 200: "Gadget"},
        item_volumes={100: 0.0, 200: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    assert trades[0].type_id == 100


def test_reconcile_retries_corp_with_later_character_that_has_accountant(monkeypatch):
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)

    class RetryClient(FakeClient):
        def corporation_wallet_transactions(self, corporation_id, division, auth_role, from_id=None):
            self.corp_txn_calls.append((corporation_id, division, auth_role, from_id))
            if auth_role == "buyer":
                raise ESIError("HTTP 403: missing Accountant")
            if from_id is not None:
                return []
            return list(self._corp_txns.get((corporation_id, division), []))

        def corporation_wallet_journal(self, corporation_id, division, auth_role):
            if auth_role == "buyer":
                raise ESIError("HTTP 403: missing Accountant")
            return list(self._corp_journal.get((corporation_id, division), []))

    corp_buy = {"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
                "location_id": JITA_4_4_STATION_ID, "transaction_id": 9001}
    corp_sell = {"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
                 "location_id": cfg.structure_id, "transaction_id": 9002}
    client = RetryClient(
        buyer_txns=[], seller_txns=[],
        character_corps={1: 99, 2: 99},
        corp_txns={(99, 1): [corp_buy, corp_sell]},
    )

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    assert any(call[2] == "seller" for call in client.corp_txn_calls)


def test_reconcile_uses_corp_journal_not_character_journal_for_corp_sell(monkeypatch):
    """A character journal entry whose id collides with a corp journal
    entry's id must not supply the corp sell's gross sale amount - the
    corp sell must resolve only through its OWN wallet's (corporation,
    corporation_id, division) namespace."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_broker_fee=0.015, jita_buy_broker_fee=0.0)
    corp_buy = {"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
                "location_id": JITA_4_4_STATION_ID, "transaction_id": 9001}
    corp_sell = {"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
                 "location_id": cfg.structure_id, "transaction_id": 9002}
    client = FakeClient(
        buyer_txns=[], seller_txns=[],
        # Decoy: same ids as the corp's own pair below, but a tiny amount,
        # in the CHARACTER journal (wrong namespace, wrong transaction_id too).
        journal_entries={2: _mt_and_tax_entries(mt_id=100, transaction_id=9002, gross=1.0,
                                                 tax_rate=0.03375, date=_iso(1))},
        character_corps={1: 99, 2: 99},
        corp_txns={(99, 1): [corp_buy, corp_sell]},
        corp_journal={(99, 1): _mt_and_tax_entries(mt_id=100, transaction_id=9002, gross=11000.0,
                                                    tax_rate=0.03375, date=_iso(1))},
    )

    trades = reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
    )

    assert len(trades) == 1
    real_tax = 11000.0 * 0.03375
    expected_net_sell = ((11000.0 - real_tax) / 10) * (1 - cfg.structure_broker_fee)
    expected_landed = 1000.0
    profit_per_unit = trades[0].realized_profit / trades[0].matched_qty
    assert round(profit_per_unit, 6) == round(expected_net_sell - expected_landed, 6)


def test_reconcile_empty_wallet_division_ids_reads_all_seven(monkeypatch):
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, wallet_division_ids=())
    client = FakeClient(
        buyer_txns=[], seller_txns=[],
        character_corps={1: 99, 2: 99},
    )
    reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={}, item_volumes={}, cfg=cfg,
    )
    assert [c[1] for c in client.corp_txn_calls] == list(WALLET_DIVISION_IDS)


def test_reconcile_configured_wallet_divisions_only(monkeypatch):
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, wallet_division_ids=(2, 5))
    client = FakeClient(
        buyer_txns=[], seller_txns=[],
        character_corps={1: 99, 2: 99},
    )
    reconcile_realized_trades(
        buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
        client=client, item_names={}, item_volumes={}, cfg=cfg,
    )
    assert [c[1] for c in client.corp_txn_calls] == [2, 5]


def test_reconcile_partial_division_access_keeps_readable_fills(monkeypatch, caplog):
    """A 403 on later wallet divisions must not discard the division that
    succeeded, and must not fall through to another member (that would
    re-fetch the readable division and double-count)."""
    monkeypatch.setattr(trade_reconciliation.storage, "get_station_ids_in_region",
                         lambda region_id: frozenset({JITA_4_4_STATION_ID}))
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0)

    class PartialClient(FakeClient):
        def corporation_wallet_transactions(self, corporation_id, division, auth_role, from_id=None):
            self.corp_txn_calls.append((corporation_id, division, auth_role, from_id))
            if division != 1:
                raise ESIError("HTTP 403: character does not have the required role")
            if from_id is not None:
                return []
            return list(self._corp_txns.get((corporation_id, division), []))

        def corporation_wallet_journal(self, corporation_id, division, auth_role):
            if division != 1:
                raise ESIError("HTTP 403: character does not have the required role")
            return list(self._corp_journal.get((corporation_id, division), []))

    corp_buy = {"is_buy": True, "type_id": 100, "date": _iso(2), "unit_price": 1000.0, "quantity": 10,
                "location_id": JITA_4_4_STATION_ID, "transaction_id": 9001}
    corp_sell = {"is_buy": False, "type_id": 100, "date": _iso(1), "unit_price": 1200.0, "quantity": 10,
                 "location_id": cfg.structure_id, "transaction_id": 9002}
    client = PartialClient(
        buyer_txns=[], seller_txns=[],
        character_corps={1: 99, 2: 99},
        corp_txns={(99, 1): [corp_buy, corp_sell]},
    )

    with caplog.at_level("WARNING", logger="eve_trader.trade_reconciliation"):
        trades = reconcile_realized_trades(
            buyer_characters=[(1, "buyer")], seller_characters=[(2, "seller")],
            client=client, item_names={100: "Widget"}, item_volumes={100: 0.0}, cfg=cfg,
        )

    assert len(trades) == 1
    assert trades[0].matched_qty == 10
    assert round(trades[0].realized_profit, 2) == round((1200.0 - 1000.0) * 10, 2)
    first_page = [c for c in client.corp_txn_calls if c[3] is None]
    assert [c[1] for c in first_page] == list(WALLET_DIVISION_IDS)
    assert {c[2] for c in client.corp_txn_calls} == {"buyer"}
    assert "could read divisions 1 but not 2, 3, 4, 5, 6, 7" in caplog.text
    assert "could read any of configured divisions" not in caplog.text

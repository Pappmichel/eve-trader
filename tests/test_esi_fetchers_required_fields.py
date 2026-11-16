"""Required ESI fields are indexed, not defaulted; optional ones stay optional."""
from __future__ import annotations

import logging

import pytest

from eve_trader.esi_data import fetchers


def test_transaction_without_is_buy_raises():
    txn = {"transaction_id": 1, "date": "2026-11-16T00:00:00Z", "type_id": 34,
           "location_id": 1, "unit_price": 1.0, "quantity": 1, "journal_ref_id": 5}
    with pytest.raises(KeyError):
        fetchers._wallet_txn_rows([txn], 0)
    assert fetchers._wallet_txn_rows([dict(txn, is_buy=False)], 0)[0][7] is False


def test_asset_without_is_singleton_raises():
    asset = {"item_id": 1, "type_id": 34, "location_id": 1, "location_flag": "Hangar",
             "quantity": 1}
    with pytest.raises(KeyError):
        fetchers._asset_rows([asset], "A")
    assert fetchers._asset_rows([dict(asset, is_singleton=True)], "A")[0][7] is True


def test_sell_order_without_is_buy_order_is_a_sell_order():
    order = {"order_id": 1, "type_id": 34, "location_id": 1, "region_id": 2, "volume_remain": 3}
    assert len(fetchers._sell_order_rows([order], "A")) == 1
    assert fetchers._sell_order_rows([dict(order, is_buy_order=True)], "A") == []


def test_journal_entry_without_amount_warns_once_and_stays_zero(caplog):
    entries = [
        {"id": 1, "date": "2026-11-16T00:00:00Z", "ref_type": "bounty_prizes"},
        {"id": 2, "date": "2026-11-16T00:00:00Z", "ref_type": "market_escrow"},
        {"id": 3, "date": "2026-11-16T00:00:00Z", "ref_type": "x", "amount": 0.0},
        {"id": 4, "date": "2026-11-16T00:00:00Z", "ref_type": "y", "amount": 5.5},
    ]
    with caplog.at_level(logging.WARNING):
        rows = fetchers._wallet_journal_rows(entries, 0)
    assert [r[4] for r in rows] == [0.0, 0.0, 0.0, 5.5]
    warnings = [r for r in caplog.records if "no amount" in r.getMessage()]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "2 of 4" in msg and "bounty_prizes" in msg and "market_escrow" in msg

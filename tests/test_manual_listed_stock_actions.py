"""Tests for production/actions.py's manual-listed-stock do_* wrappers
(docs/MANUAL_TRACKING_PLAN.md phase 7) - unit-level, storage monkeypatched,
no Postgres needed. See test_manual_listed_stock.py for storage.py's own
functions."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions

TYPE_ID = 34  # Tritanium


def test_do_set_manual_listed_stock_rejects_bad_market():
    with pytest.raises(ActionError, match="Market must be"):
        actions.do_set_manual_listed_stock(TYPE_ID, "bogus", 10)


def test_do_set_manual_listed_stock_rejects_negative_quantity():
    with pytest.raises(ActionError, match="cannot be negative"):
        actions.do_set_manual_listed_stock(TYPE_ID, "home", -1)


def test_do_set_manual_listed_stock_passes_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "upsert_manual_listed_stock",
                         lambda type_id, market, quantity: captured.update(
                             type_id=type_id, market=market, quantity=quantity))

    result = actions.do_set_manual_listed_stock(TYPE_ID, "home", 100.0)

    assert captured == {"type_id": TYPE_ID, "market": "home", "quantity": 100.0}
    assert result == {"type_id": TYPE_ID, "market": "home", "quantity": 100.0}


def test_do_clear_manual_listed_stock_passes_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "delete_manual_listed_stock",
                         lambda type_id, market: captured.update(type_id=type_id, market=market))

    result = actions.do_clear_manual_listed_stock(TYPE_ID, "jita")

    assert captured == {"type_id": TYPE_ID, "market": "jita"}
    assert result == {"type_id": TYPE_ID, "market": "jita"}


def test_do_list_manual_listed_stock_maps_rows(monkeypatch):
    monkeypatch.setattr(storage, "load_manual_listed_stock", lambda: {
        (TYPE_ID, "home"): (100.0, "2026-01-01T00:00:00+00:00"),
    })

    result = actions.do_list_manual_listed_stock()

    assert result == {"rows": [
        {"type_id": TYPE_ID, "market": "home", "quantity": 100.0, "updated_at": "2026-01-01T00:00:00+00:00"},
    ]}

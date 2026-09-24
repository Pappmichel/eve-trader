"""Tests for production/actions.py's manual-stock-entries do_* wrappers
(docs/MANUAL_TRACKING_PLAN.md phase 3) - unit-level, storage monkeypatched,
no Postgres needed. See test_manual_stock_entries.py for storage.py's own
per-location functions."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions

TYPE_ID = 34  # Tritanium
LOCATION_A = 1000000000001


def test_do_set_manual_stock_rejects_negative_count():
    with pytest.raises(ActionError, match="cannot be negative"):
        actions.do_set_manual_stock(TYPE_ID, -1)


def test_do_set_manual_stock_passes_location_id_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "upsert_manual_stock",
                         lambda type_id, count, location_id=0: captured.update(
                             type_id=type_id, count=count, location_id=location_id))

    result = actions.do_set_manual_stock(TYPE_ID, 100, location_id=LOCATION_A)

    assert captured == {"type_id": TYPE_ID, "count": 100, "location_id": LOCATION_A}
    assert result == {"type_id": TYPE_ID, "count": 100, "location_id": LOCATION_A}


def test_do_set_manual_stock_defaults_location_id_to_zero(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "upsert_manual_stock",
                         lambda type_id, count, location_id=0: captured.update(location_id=location_id))

    actions.do_set_manual_stock(TYPE_ID, 100)

    assert captured == {"location_id": 0}


def test_do_list_manual_stock_entries_maps_rows_to_dicts(monkeypatch):
    monkeypatch.setattr(storage, "load_manual_stock_entries",
                         lambda: [(TYPE_ID, "Tritanium", LOCATION_A, 100.0)])

    result = actions.do_list_manual_stock_entries()

    assert result == {"rows": [
        {"type_id": TYPE_ID, "type_name": "Tritanium", "location_id": LOCATION_A, "count": 100.0},
    ]}


def test_do_add_manual_stock_entry_resolves_exact_name(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [(TYPE_ID, "Tritanium")])
    captured = {}
    monkeypatch.setattr(storage, "upsert_manual_stock",
                         lambda type_id, count, location_id=0: captured.update(
                             type_id=type_id, count=count, location_id=location_id))

    result = actions.do_add_manual_stock_entry("Tritanium", 100, LOCATION_A)

    assert captured == {"type_id": TYPE_ID, "count": 100, "location_id": LOCATION_A}
    assert result == {"type_id": TYPE_ID, "type_name": "Tritanium", "location_id": LOCATION_A, "count": 100}


def test_do_add_manual_stock_entry_rejects_negative_count(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [(TYPE_ID, "Tritanium")])
    with pytest.raises(ActionError, match="cannot be negative"):
        actions.do_add_manual_stock_entry("Tritanium", -1, LOCATION_A)


def test_do_add_manual_stock_entry_rejects_unknown_name(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [])
    with pytest.raises(ActionError, match="No type found"):
        actions.do_add_manual_stock_entry("Not A Real Item", 1, 0)


def test_do_add_manual_stock_entry_rejects_ambiguous_name(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [(99, "Tritanium Ore Sample")])
    with pytest.raises(ActionError, match="Did you mean"):
        actions.do_add_manual_stock_entry("Tritanium", 1, 0)


def test_do_remove_manual_stock_entry_passes_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "delete_manual_stock",
                         lambda type_id, location_id=0: captured.update(type_id=type_id, location_id=location_id))

    result = actions.do_remove_manual_stock_entry(TYPE_ID, LOCATION_A)

    assert captured == {"type_id": TYPE_ID, "location_id": LOCATION_A}
    assert result == {"type_id": TYPE_ID, "location_id": LOCATION_A}

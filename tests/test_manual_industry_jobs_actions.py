"""Tests for production/actions.py's manual-industry-job do_* wrappers
(docs/MANUAL_TRACKING_PLAN.md phase 6) - unit-level, storage monkeypatched,
no Postgres needed. See test_manual_industry_jobs.py for storage.py's own
functions."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions

PRODUCT_TYPE_ID = 587  # Rifter
BP_TYPE_ID = 690
LOCATION_A = 1000000000001
LOCATION_B = 1000000000002


def _stub_search(monkeypatch, matches):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=2: matches)


def test_do_add_manual_industry_job_converts_runs_to_quantity(monkeypatch):
    _stub_search(monkeypatch, [(PRODUCT_TYPE_ID, "Rifter")])
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (BP_TYPE_ID, 1, 5.0))
    captured = {}
    monkeypatch.setattr(storage, "insert_manual_industry_job",
                         lambda *args: captured.update(args=args) or 7)

    result = actions.do_add_manual_industry_job("Rifter", runs=10, location_id=LOCATION_A)

    assert captured["args"] == (PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None)
    assert result == {
        "manual_id": 7, "type_id": PRODUCT_TYPE_ID, "type_name": "Rifter", "activity_id": 1,
        "quantity": 50.0, "runs": 10, "location_id": LOCATION_A, "ready_at": None,
    }


def test_do_add_manual_industry_job_accepts_raw_quantity(monkeypatch):
    _stub_search(monkeypatch, [(PRODUCT_TYPE_ID, "Rifter")])
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (BP_TYPE_ID, 1, 5.0))
    captured = {}
    monkeypatch.setattr(storage, "insert_manual_industry_job",
                         lambda *args: captured.update(args=args) or 7)

    result = actions.do_add_manual_industry_job("Rifter", quantity=42.0, location_id=LOCATION_A)

    assert captured["args"] == (PRODUCT_TYPE_ID, 1, 42.0, None, LOCATION_A, None)
    assert result["runs"] is None
    assert result["quantity"] == 42.0


def test_do_add_manual_industry_job_rejects_both_quantity_and_runs(monkeypatch):
    _stub_search(monkeypatch, [(PRODUCT_TYPE_ID, "Rifter")])
    with pytest.raises(ActionError, match="exactly one"):
        actions.do_add_manual_industry_job("Rifter", quantity=1, runs=1)


def test_do_add_manual_industry_job_rejects_neither_quantity_nor_runs(monkeypatch):
    _stub_search(monkeypatch, [(PRODUCT_TYPE_ID, "Rifter")])
    with pytest.raises(ActionError, match="exactly one"):
        actions.do_add_manual_industry_job("Rifter")


def test_do_add_manual_industry_job_rejects_item_with_no_blueprint(monkeypatch):
    _stub_search(monkeypatch, [(34, "Tritanium")])
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: None)
    with pytest.raises(ActionError, match="no known blueprint"):
        actions.do_add_manual_industry_job("Tritanium", runs=1)


def test_do_add_manual_industry_job_rejects_unknown_name(monkeypatch):
    _stub_search(monkeypatch, [])
    with pytest.raises(ActionError, match="No type found"):
        actions.do_add_manual_industry_job("Bogus Item", runs=1)


def test_do_add_manual_industry_job_rejects_zero_runs(monkeypatch):
    _stub_search(monkeypatch, [(PRODUCT_TYPE_ID, "Rifter")])
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (BP_TYPE_ID, 1, 5.0))
    with pytest.raises(ActionError, match="Runs must be positive"):
        actions.do_add_manual_industry_job("Rifter", runs=0)


def test_do_add_manual_industry_job_rejects_zero_quantity(monkeypatch):
    _stub_search(monkeypatch, [(PRODUCT_TYPE_ID, "Rifter")])
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (BP_TYPE_ID, 1, 5.0))
    with pytest.raises(ActionError, match="Quantity must be positive"):
        actions.do_add_manual_industry_job("Rifter", quantity=0)


def test_do_update_manual_industry_job_rejects_missing_id(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_industry_job", lambda manual_id: None)
    with pytest.raises(ActionError, match="No manual job entry"):
        actions.do_update_manual_industry_job(999, runs=1)


def test_do_update_manual_industry_job_defaults_location_to_existing(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_industry_job",
                         lambda manual_id: (7, PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None))
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (BP_TYPE_ID, 1, 5.0))
    captured = {}
    monkeypatch.setattr(storage, "update_manual_industry_job",
                         lambda *args: captured.update(args=args))

    result = actions.do_update_manual_industry_job(7, runs=20)

    assert captured["args"] == (7, 100.0, 20, LOCATION_A, None)
    assert result["location_id"] == LOCATION_A


def test_do_update_manual_industry_job_overrides_location(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_industry_job",
                         lambda manual_id: (7, PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None))
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (BP_TYPE_ID, 1, 5.0))
    monkeypatch.setattr(storage, "update_manual_industry_job", lambda *args: None)

    result = actions.do_update_manual_industry_job(7, runs=20, location_id=LOCATION_B)

    assert result["location_id"] == LOCATION_B


def test_do_remove_manual_industry_job(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "delete_manual_industry_job", lambda manual_id: captured.update(id=manual_id))

    result = actions.do_remove_manual_industry_job(7)

    assert captured == {"id": 7}
    assert result == {"manual_id": 7}


def test_do_complete_manual_industry_job_rejects_missing_id(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_industry_job", lambda manual_id: None)
    with pytest.raises(ActionError, match="No manual job entry"):
        actions.do_complete_manual_industry_job(999)


def test_do_complete_manual_industry_job_defaults_to_job_s_own_location(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_industry_job",
                         lambda manual_id: (7, PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None))
    captured = {}
    monkeypatch.setattr(storage, "complete_manual_job",
                         lambda job_id, location_id: captured.update(job_id=job_id, location_id=location_id))

    result = actions.do_complete_manual_industry_job(7)

    assert captured == {"job_id": 7, "location_id": LOCATION_A}
    assert result == {"manual_id": 7, "location_id": LOCATION_A}


def test_do_complete_manual_industry_job_overrides_location(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_industry_job",
                         lambda manual_id: (7, PRODUCT_TYPE_ID, 1, 50.0, 10, LOCATION_A, None))
    captured = {}
    monkeypatch.setattr(storage, "complete_manual_job",
                         lambda job_id, location_id: captured.update(location_id=location_id))

    result = actions.do_complete_manual_industry_job(7, location_id=LOCATION_B)

    assert captured == {"location_id": LOCATION_B}
    assert result == {"manual_id": 7, "location_id": LOCATION_B}

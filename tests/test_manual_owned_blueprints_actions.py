"""Tests for production/actions.py's manual-owned-blueprint do_* wrappers
(docs/MANUAL_TRACKING_PLAN.md phase 5) - unit-level, storage monkeypatched,
no Postgres needed. See test_manual_owned_blueprints.py for storage.py's
own functions."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions

BP_TYPE_ID = 690  # Rifter Blueprint
PRODUCT_TYPE_ID = 587  # Rifter
LOCATION_A = 1000000000001


def _stub_search(monkeypatch, matches):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=2: matches)


def _stub_invalidation(monkeypatch):
    monkeypatch.setattr(actions, "invalidate_discover_cache", lambda: None)
    monkeypatch.setattr(actions, "invalidate_ship_margin_cache", lambda: None)


def test_do_add_manual_owned_blueprint_accepts_blueprint_name_directly(monkeypatch):
    _stub_search(monkeypatch, [(BP_TYPE_ID, "Rifter Blueprint")])
    monkeypatch.setattr(storage, "is_known_blueprint", lambda type_id: True)
    monkeypatch.setattr(storage, "get_blueprint_for_product",
                         lambda type_id: pytest.fail("must not be called when the name is already a blueprint"))
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Rifter Blueprint", 0, 1, None, None))
    captured = {}
    monkeypatch.setattr(storage, "insert_manual_owned_blueprint",
                         lambda *args: captured.update(args=args) or 7)
    _stub_invalidation(monkeypatch)

    result = actions.do_add_manual_owned_blueprint("Rifter Blueprint", True, 10, 20, None, 1, LOCATION_A)

    assert captured["args"] == (BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A)
    assert result["manual_id"] == 7
    assert result["type_id"] == BP_TYPE_ID


def test_do_add_manual_owned_blueprint_accepts_product_name(monkeypatch):
    _stub_search(monkeypatch, [(PRODUCT_TYPE_ID, "Rifter")])
    monkeypatch.setattr(storage, "is_known_blueprint", lambda type_id: False)
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: (BP_TYPE_ID, 1, 1.0))
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Rifter Blueprint", 0, 1, None, None))
    captured = {}
    monkeypatch.setattr(storage, "insert_manual_owned_blueprint",
                         lambda *args: captured.update(args=args) or 7)
    _stub_invalidation(monkeypatch)

    result = actions.do_add_manual_owned_blueprint("Rifter", True, 10, 20, None, 1, LOCATION_A)

    assert captured["args"][0] == BP_TYPE_ID  # mapped to the blueprint, not the product
    assert result["type_id"] == BP_TYPE_ID


def test_do_add_manual_owned_blueprint_rejects_non_producible_item(monkeypatch):
    _stub_search(monkeypatch, [(34, "Tritanium")])
    monkeypatch.setattr(storage, "is_known_blueprint", lambda type_id: False)
    monkeypatch.setattr(storage, "get_blueprint_for_product", lambda type_id: None)

    with pytest.raises(ActionError, match="not a known blueprint"):
        actions.do_add_manual_owned_blueprint("Tritanium", True, 10, 20, None, 1, LOCATION_A)


def test_do_add_manual_owned_blueprint_rejects_unknown_name(monkeypatch):
    _stub_search(monkeypatch, [])
    with pytest.raises(ActionError, match="No type found"):
        actions.do_add_manual_owned_blueprint("Bogus Item", True, 10, 20, None, 1, LOCATION_A)


def test_do_add_manual_owned_blueprint_rejects_ambiguous_name(monkeypatch):
    _stub_search(monkeypatch, [(999, "Rifter Something Else")])
    with pytest.raises(ActionError, match="Did you mean"):
        actions.do_add_manual_owned_blueprint("Rifter", True, 10, 20, None, 1, LOCATION_A)


@pytest.mark.parametrize("me,te", [(-1, 10), (11, 10), (5, -2), (5, 22), (5, 3)])
def test_do_add_manual_owned_blueprint_rejects_bad_me_te(monkeypatch, me, te):
    _stub_search(monkeypatch, [(BP_TYPE_ID, "Rifter Blueprint")])
    monkeypatch.setattr(storage, "is_known_blueprint", lambda type_id: True)
    with pytest.raises(ActionError):
        actions.do_add_manual_owned_blueprint("Rifter Blueprint", True, me, te, None, 1, LOCATION_A)


def test_do_add_manual_owned_blueprint_bpo_rejects_runs(monkeypatch):
    _stub_search(monkeypatch, [(BP_TYPE_ID, "Rifter Blueprint")])
    monkeypatch.setattr(storage, "is_known_blueprint", lambda type_id: True)
    with pytest.raises(ActionError, match="no runs"):
        actions.do_add_manual_owned_blueprint("Rifter Blueprint", True, 10, 20, 5, 1, LOCATION_A)


def test_do_add_manual_owned_blueprint_bpc_requires_runs(monkeypatch):
    _stub_search(monkeypatch, [(BP_TYPE_ID, "Rifter Blueprint")])
    monkeypatch.setattr(storage, "is_known_blueprint", lambda type_id: True)
    with pytest.raises(ActionError, match="needs runs"):
        actions.do_add_manual_owned_blueprint("Rifter Blueprint", False, 4, 8, None, 1, LOCATION_A)


def test_do_add_manual_owned_blueprint_rejects_bad_quantity(monkeypatch):
    _stub_search(monkeypatch, [(BP_TYPE_ID, "Rifter Blueprint")])
    monkeypatch.setattr(storage, "is_known_blueprint", lambda type_id: True)
    with pytest.raises(ActionError, match="Quantity"):
        actions.do_add_manual_owned_blueprint("Rifter Blueprint", True, 10, 20, None, 0, LOCATION_A)


def test_do_add_manual_owned_blueprint_invalidates_caches(monkeypatch):
    _stub_search(monkeypatch, [(BP_TYPE_ID, "Rifter Blueprint")])
    monkeypatch.setattr(storage, "is_known_blueprint", lambda type_id: True)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Rifter Blueprint", 0, 1, None, None))
    monkeypatch.setattr(storage, "insert_manual_owned_blueprint", lambda *a: 1)
    invalidated = []
    monkeypatch.setattr(actions, "invalidate_discover_cache", lambda: invalidated.append("discover"))
    monkeypatch.setattr(actions, "invalidate_ship_margin_cache", lambda: invalidated.append("ship_margin"))

    actions.do_add_manual_owned_blueprint("Rifter Blueprint", True, 10, 20, None, 1, LOCATION_A)

    assert invalidated == ["discover", "ship_margin"]


def test_do_update_manual_owned_blueprint_rejects_missing_id(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_owned_blueprint", lambda manual_id: None)
    with pytest.raises(ActionError, match="No manual blueprint entry"):
        actions.do_update_manual_owned_blueprint(999, 10, 20, None, 1)


def test_do_update_manual_owned_blueprint_bpo_rejects_runs(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_owned_blueprint",
                         lambda manual_id: (7, BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A))
    with pytest.raises(ActionError, match="no runs"):
        actions.do_update_manual_owned_blueprint(7, 10, 20, 5, 1)


def test_do_update_manual_owned_blueprint_bpc_requires_runs(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_owned_blueprint",
                         lambda manual_id: (7, BP_TYPE_ID, False, 4, 8, 5, 1, LOCATION_A))
    with pytest.raises(ActionError, match="needs runs"):
        actions.do_update_manual_owned_blueprint(7, 4, 8, None, 1)


def test_do_update_manual_owned_blueprint_updates_and_invalidates(monkeypatch):
    monkeypatch.setattr(storage, "get_manual_owned_blueprint",
                         lambda manual_id: (7, BP_TYPE_ID, False, 4, 8, 5, 1, LOCATION_A))
    captured = {}
    monkeypatch.setattr(storage, "update_manual_owned_blueprint",
                         lambda *args: captured.update(args=args))
    invalidated = []
    monkeypatch.setattr(actions, "invalidate_discover_cache", lambda: invalidated.append("discover"))
    monkeypatch.setattr(actions, "invalidate_ship_margin_cache", lambda: invalidated.append("ship_margin"))

    result = actions.do_update_manual_owned_blueprint(7, 6, 12, 10, 3)

    assert captured["args"] == (7, 6, 12, 10, 3)
    assert invalidated == ["discover", "ship_margin"]
    assert result == {"manual_id": 7, "material_efficiency": 6, "time_efficiency": 12, "runs": 10, "quantity": 3}


def test_do_remove_manual_owned_blueprint_invalidates_caches(monkeypatch):
    monkeypatch.setattr(storage, "delete_manual_owned_blueprint", lambda manual_id: None)
    invalidated = []
    monkeypatch.setattr(actions, "invalidate_discover_cache", lambda: invalidated.append("discover"))
    monkeypatch.setattr(actions, "invalidate_ship_margin_cache", lambda: invalidated.append("ship_margin"))

    result = actions.do_remove_manual_owned_blueprint(7)

    assert result == {"manual_id": 7}
    assert invalidated == ["discover", "ship_margin"]

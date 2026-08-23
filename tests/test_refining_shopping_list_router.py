"""Router-level tests for the Mineral Shopping List endpoints (GitHub issue
#93) under /api/refining/shopping-list/*. Same pattern as
test_refining_router.py: every do_* call is monkeypatched on the already-
imported eve_trader.refining.actions module object, so nothing here touches
the real DB/ESI.
"""
from fastapi.testclient import TestClient

from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.refining import actions as refining_actions

client = TestClient(create_app())

_PLAN = {
    "ore_purchases": [{"type_id": 28430, "item": "Compressed Veldspar", "family": "Veldspar", "is_ice": False,
                        "portions": 10, "units": 1000, "volume_m3": 150.0,
                        "landed_cost_per_unit": 10.0, "total_cost": 10000.0}],
    "direct_purchases": [{"type_id": 35, "name": "Pyerite", "quantity": 100,
                           "landed_cost_per_unit": 12.0, "total_cost": 1200.0, "source": "Home"}],
    "coverage": [{"type_id": 34, "name": "Tritanium", "required": 4000.0, "from_ore": 4150,
                   "from_direct": 0, "delivered": 4150, "surplus": 150.0}],
    "ore_cost": 10000.0, "direct_cost": 1200.0, "total_cost": 11200.0, "lp_cost": 10900.0,
    "all_direct_cost": 25200.0, "savings_vs_all_direct": 14000.0, "total_volume_m3": 150.0,
}


def test_get_refinable_minerals_calls_action(monkeypatch):
    monkeypatch.setattr(refining_actions, "do_list_refinable_minerals",
                        lambda: [{"type_id": 34, "name": "Tritanium"}])
    resp = client.get("/api/refining/shopping-list/minerals")
    assert resp.status_code == 200
    assert resp.json() == [{"type_id": 34, "name": "Tritanium"}]


def test_get_mineral_requirements_calls_action(monkeypatch):
    monkeypatch.setattr(refining_actions, "do_load_mineral_requirements",
                        lambda: [{"type_id": 34, "name": "Tritanium", "required_qty": 1000.0}])
    resp = client.get("/api/refining/shopping-list/requirements")
    assert resp.status_code == 200
    assert resp.json() == [{"type_id": 34, "name": "Tritanium", "required_qty": 1000.0}]


def test_save_mineral_requirements_passes_the_list_to_the_action(monkeypatch):
    captured = {}

    def _save(requirements):
        captured["requirements"] = requirements
        return {"saved": len(requirements)}
    monkeypatch.setattr(refining_actions, "do_save_mineral_requirements", _save)

    resp = client.post("/api/refining/shopping-list/requirements",
                        json={"requirements": [{"type_id": 34, "required_qty": 1000}]})

    assert resp.status_code == 200
    assert resp.json() == {"saved": 1}
    assert captured["requirements"] == [{"type_id": 34, "name": None, "required_qty": 1000.0}]


def test_save_mineral_requirements_accepts_an_empty_list(monkeypatch):
    monkeypatch.setattr(refining_actions, "do_save_mineral_requirements",
                        lambda requirements: {"saved": len(requirements)})
    resp = client.post("/api/refining/shopping-list/requirements", json={"requirements": []})
    assert resp.status_code == 200
    assert resp.json() == {"saved": 0}


def test_save_mineral_requirements_action_error_maps_to_400(monkeypatch):
    def _raise(requirements):
        raise ActionError("Required quantity for type 34 must be greater than 0.")
    monkeypatch.setattr(refining_actions, "do_save_mineral_requirements", _raise)

    resp = client.post("/api/refining/shopping-list/requirements",
                        json={"requirements": [{"type_id": 34, "required_qty": 0}]})

    assert resp.status_code == 400
    assert "greater than 0" in resp.json()["detail"]


def test_optimize_shopping_list_without_a_body_solves_the_saved_list(monkeypatch):
    captured = {}

    def _optimize(requirements):
        captured["requirements"] = requirements
        return _PLAN
    monkeypatch.setattr(refining_actions, "do_optimize_mineral_shopping_list", _optimize)

    resp = client.post("/api/refining/shopping-list/optimize")

    assert resp.status_code == 200
    assert captured["requirements"] is None
    assert resp.json()["total_cost"] == 11200.0
    assert resp.json()["ore_purchases"][0]["item"] == "Compressed Veldspar"
    # GitHub issue #111: schemas.DirectMineralPurchase used to have no
    # `source` field, so FastAPI's response_model silently dropped it -
    # the frontend's "Source" column then always showed "-", even though
    # the action itself had computed "Home"/"Jita" correctly.
    assert resp.json()["direct_purchases"][0]["source"] == "Home"


def test_optimize_shopping_list_passes_an_ad_hoc_list_through(monkeypatch):
    captured = {}

    def _optimize(requirements):
        captured["requirements"] = requirements
        return _PLAN
    monkeypatch.setattr(refining_actions, "do_optimize_mineral_shopping_list", _optimize)

    resp = client.post("/api/refining/shopping-list/optimize",
                        json={"requirements": [{"type_id": 34, "name": "Tritanium", "required_qty": 4000}]})

    assert resp.status_code == 200
    assert captured["requirements"] == [{"type_id": 34, "name": "Tritanium", "required_qty": 4000.0}]


def test_optimize_shopping_list_action_error_maps_to_400(monkeypatch):
    def _raise(requirements):
        raise ActionError("No mineral requirements yet - add at least one mineral and quantity first.")
    monkeypatch.setattr(refining_actions, "do_optimize_mineral_shopping_list", _raise)

    resp = client.post("/api/refining/shopping-list/optimize")

    assert resp.status_code == 400
    assert "No mineral requirements yet" in resp.json()["detail"]

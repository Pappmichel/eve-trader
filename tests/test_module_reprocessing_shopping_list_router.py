"""Router-level tests for Module Reprocessing's Mineral Shopping List
endpoints under /api/module-reprocessing/shopping-list/*. Same pattern as
test_refining_shopping_list_router.py: every do_* call is monkeypatched on the
already-imported eve_trader.module_reprocessing.actions module object, so
nothing here touches the real DB/ESI.
"""
from fastapi.testclient import TestClient

from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.module_reprocessing import actions as mr_actions

client = TestClient(create_app())

_PLAN = {
    "reprocess_purchases": [
        {"type_id": 28430, "item": "Compressed Veldspar", "category": "ore", "family": "Veldspar",
         "is_ice": False, "portions": 10, "units": 1000, "volume_m3": 150.0,
         "landed_cost_per_unit": 10.0, "total_cost": 10000.0},
        {"type_id": 9071, "item": "200mm AutoCannon I", "category": "module", "family": None,
         "is_ice": False, "portions": 5, "units": 5, "volume_m3": 25.0,
         "landed_cost_per_unit": 60.0, "total_cost": 300.0},
    ],
    "direct_purchases": [{"type_id": 35, "name": "Pyerite", "quantity": 100,
                           "landed_cost_per_unit": 12.0, "total_cost": 1200.0, "source": "Home"}],
    "coverage": [{"type_id": 34, "name": "Tritanium", "required": 4000.0, "from_reprocessing": 4150,
                   "from_direct": 0, "delivered": 4150, "surplus": 150.0}],
    "reprocess_cost": 10300.0, "direct_cost": 1200.0, "total_cost": 11500.0, "lp_cost": 11200.0,
    "all_direct_cost": 25200.0, "savings_vs_all_direct": 13700.0, "total_volume_m3": 175.0,
}


def test_get_shoppable_minerals_calls_action(monkeypatch):
    monkeypatch.setattr(mr_actions, "do_list_shoppable_minerals", lambda: [{"type_id": 34, "name": "Tritanium"}])
    resp = client.get("/api/module-reprocessing/shopping-list/minerals")
    assert resp.status_code == 200
    assert resp.json() == [{"type_id": 34, "name": "Tritanium"}]


def test_get_shopping_requirements_calls_action(monkeypatch):
    monkeypatch.setattr(mr_actions, "do_load_module_shopping_requirements",
                        lambda: [{"type_id": 34, "name": "Tritanium", "required_qty": 1000.0}])
    resp = client.get("/api/module-reprocessing/shopping-list/requirements")
    assert resp.status_code == 200
    assert resp.json() == [{"type_id": 34, "name": "Tritanium", "required_qty": 1000.0}]


def test_save_shopping_requirements_passes_the_list_to_the_action(monkeypatch):
    captured = {}

    def _save(requirements):
        captured["requirements"] = requirements
        return {"saved": len(requirements)}
    monkeypatch.setattr(mr_actions, "do_save_module_shopping_requirements", _save)

    resp = client.post("/api/module-reprocessing/shopping-list/requirements",
                        json={"requirements": [{"type_id": 34, "required_qty": 1000}]})

    assert resp.status_code == 200
    assert resp.json() == {"saved": 1}
    assert captured["requirements"] == [{"type_id": 34, "name": None, "required_qty": 1000.0}]


def test_save_shopping_requirements_accepts_an_empty_list(monkeypatch):
    monkeypatch.setattr(mr_actions, "do_save_module_shopping_requirements",
                        lambda requirements: {"saved": len(requirements)})
    resp = client.post("/api/module-reprocessing/shopping-list/requirements", json={"requirements": []})
    assert resp.status_code == 200
    assert resp.json() == {"saved": 0}


def test_save_shopping_requirements_action_error_maps_to_400(monkeypatch):
    def _raise(requirements):
        raise ActionError("Required quantity for type 34 must be greater than 0.")
    monkeypatch.setattr(mr_actions, "do_save_module_shopping_requirements", _raise)

    resp = client.post("/api/module-reprocessing/shopping-list/requirements",
                        json={"requirements": [{"type_id": 34, "required_qty": 0}]})

    assert resp.status_code == 400
    assert "greater than 0" in resp.json()["detail"]


def test_optimize_without_a_body_solves_the_saved_list(monkeypatch):
    captured = {}

    def _optimize(requirements):
        captured["requirements"] = requirements
        return _PLAN
    monkeypatch.setattr(mr_actions, "do_optimize_module_shopping_list", _optimize)

    resp = client.post("/api/module-reprocessing/shopping-list/optimize")

    assert resp.status_code == 200
    assert captured["requirements"] is None
    body = resp.json()
    assert body["total_cost"] == 11500.0
    assert body["reprocess_cost"] == 10300.0
    # Every field the frontend filters/labels on must survive response_model
    # filtering (see GitHub issue #111's lesson on schemas.DirectMineralPurchase).
    assert [(p["category"], p["family"]) for p in body["reprocess_purchases"]] == [
        ("ore", "Veldspar"), ("module", None)]
    assert body["coverage"][0]["from_reprocessing"] == 4150
    assert body["direct_purchases"][0]["source"] == "Home"


def test_optimize_passes_an_ad_hoc_list_through(monkeypatch):
    captured = {}

    def _optimize(requirements):
        captured["requirements"] = requirements
        return _PLAN
    monkeypatch.setattr(mr_actions, "do_optimize_module_shopping_list", _optimize)

    resp = client.post("/api/module-reprocessing/shopping-list/optimize",
                        json={"requirements": [{"type_id": 34, "name": "Tritanium", "required_qty": 4000}]})

    assert resp.status_code == 200
    assert captured["requirements"] == [{"type_id": 34, "name": "Tritanium", "required_qty": 4000.0}]


def test_optimize_action_error_maps_to_400(monkeypatch):
    def _raise(requirements):
        raise ActionError("No mineral requirements yet - add at least one mineral and quantity first.")
    monkeypatch.setattr(mr_actions, "do_optimize_module_shopping_list", _raise)

    resp = client.post("/api/module-reprocessing/shopping-list/optimize")

    assert resp.status_code == 400
    assert "No mineral requirements yet" in resp.json()["detail"]

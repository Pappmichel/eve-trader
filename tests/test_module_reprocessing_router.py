"""Router-level tests for api/routers/module_reprocessing.py. Same pattern as
test_refining_router.py: every do_* call and storage read is monkeypatched,
never touches the real DB/ESI/Goonmetrics."""
import pandas as pd
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.module_reprocessing import actions as mr_actions

client = TestClient(create_app())


def test_get_shortlist_snapshot_reads_latest_snapshot(monkeypatch):
    df = pd.DataFrame([{
        "item_id": 100, "item": "200mm AutoCannon I", "active": True, "volume_m3": 0.01, "landed_cost": 1.9,
        "yield_pct": 0.5, "mineral_value": 473.15, "refining_tax": 0.0, "net_sell": 473.15,
        "sell_listed_qty": 50.0, "profit_per_unit": 471.24, "margin": 247.0, "profit_per_m3": 47124.0,
        "decision": "Import",
    }])
    monkeypatch.setattr(storage, "latest_module_reprocessing_snapshot", lambda: df)

    resp = client.get("/api/module-reprocessing/shortlist/snapshot")

    assert resp.status_code == 200
    assert resp.json()[0]["item"] == "200mm AutoCannon I"


def test_get_shortlist_snapshot_empty_returns_empty_list(monkeypatch):
    monkeypatch.setattr(storage, "latest_module_reprocessing_snapshot", lambda: pd.DataFrame())
    resp = client.get("/api/module-reprocessing/shortlist/snapshot")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_shortlist_items(monkeypatch):
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist", lambda: [(100, "200mm AutoCannon I", True)])
    resp = client.get("/api/module-reprocessing/shortlist/items")
    assert resp.status_code == 200
    assert resp.json() == [{"item_id": 100, "item": "200mm AutoCannon I", "active": True}]


def test_get_discovered_candidates(monkeypatch):
    monkeypatch.setattr(mr_actions, "do_get_discovered_candidates", lambda: [{
        "type_id": 100, "item": "200mm AutoCannon I", "volume_m3": 0.01, "est_landed_cost": 1.9,
        "est_mineral_value": 473.15, "est_profit_per_unit": 471.24, "est_margin": 247.0,
    }])
    resp = client.get("/api/module-reprocessing/discover/results")
    assert resp.status_code == 200
    assert resp.json()[0]["type_id"] == 100


def test_discover_calls_action(monkeypatch):
    monkeypatch.setattr(mr_actions, "do_discover_candidates", lambda: {"scanned": 500, "estimated_profitable": 12})
    resp = client.post("/api/module-reprocessing/discover")
    assert resp.status_code == 200
    assert resp.json() == {"scanned": 500, "estimated_profitable": 12}


def test_discover_action_error_maps_to_400(monkeypatch):
    def _raise():
        raise ActionError("No T1/Meta module or drone types found in the SDE cache - run Refresh SDE first.")
    monkeypatch.setattr(mr_actions, "do_discover_candidates", _raise)

    resp = client.post("/api/module-reprocessing/discover")

    assert resp.status_code == 400
    assert "Refresh SDE" in resp.json()["detail"]


def test_add_to_shortlist_passes_item_ids_to_action(monkeypatch):
    captured = {}

    def _add(item_ids):
        captured["item_ids"] = item_ids
        return {"added": len(item_ids)}
    monkeypatch.setattr(mr_actions, "do_add_to_shortlist", _add)

    resp = client.post("/api/module-reprocessing/shortlist/add", json={"item_ids": [100, 200]})

    assert resp.status_code == 200
    assert captured["item_ids"] == [100, 200]
    assert resp.json() == {"added": 2}


def test_deactivate_shortlist_items_passes_item_ids_to_action(monkeypatch):
    captured = {}

    def _deactivate(item_ids):
        captured["item_ids"] = item_ids
        return {"deactivated": len(item_ids)}
    monkeypatch.setattr(mr_actions, "do_deactivate_shortlist_items", _deactivate)

    resp = client.post("/api/module-reprocessing/shortlist/deactivate", json={"item_ids": [100]})

    assert resp.status_code == 200
    assert captured["item_ids"] == [100]
    assert resp.json() == {"deactivated": 1}


def test_activate_shortlist_items_passes_item_ids_to_action(monkeypatch):
    captured = {}

    def _activate(item_ids):
        captured["item_ids"] = item_ids
        return {"activated": len(item_ids)}
    monkeypatch.setattr(mr_actions, "do_activate_shortlist_items", _activate)

    resp = client.post("/api/module-reprocessing/shortlist/activate", json={"item_ids": [100]})

    assert resp.status_code == 200
    assert captured["item_ids"] == [100]
    assert resp.json() == {"activated": 1}


def test_refresh_shortlist_action_error_maps_to_400(monkeypatch):
    def _raise():
        raise ActionError("Shortlist is empty - add candidates from Discover first.")
    monkeypatch.setattr(mr_actions, "do_refresh_shortlist", _raise)

    resp = client.post("/api/module-reprocessing/shortlist/refresh")

    assert resp.status_code == 400
    assert "Discover first" in resp.json()["detail"]


def test_update_settings_calls_action(monkeypatch):
    captured = {}

    def _update(updates):
        captured["updates"] = updates
        return updates
    monkeypatch.setattr(mr_actions, "do_update_settings", _update)

    payload = {
        "scrapmetal_processing_skill_level": 5, "refining_tax_rate": 0.02, "freight_cost_per_m3": 500.0,
        "min_profit_threshold": 0.0, "min_margin_threshold": 0.05, "purchase_region_id": 10000002,
        "purchase_structure_id": None,
    }
    resp = client.post("/api/module-reprocessing/settings", json=payload)

    assert resp.status_code == 200
    assert captured["updates"] == payload

"""Router-level tests for api/routers/refining.py - GitHub issue #91. Same
pattern as test_doctrine_router.py: every do_* call and storage read is
monkeypatched, never touches the real DB/ESI."""
import pandas as pd
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.refining import actions as refining_actions

client = TestClient(create_app())


def test_get_shortlist_snapshot_reads_latest_ore_snapshot(monkeypatch):
    df = pd.DataFrame([{
        "item_id": 34, "item": "Compressed Veldspar", "family": "Veldspar", "is_ice": False, "active": True,
        "volume_m3": 0.01, "landed_cost": 1.9, "yield_pct": 0.5, "mineral_value": 1958.8, "refining_tax": 0.0,
        "net_sell": 1958.8, "sell_listed_qty": 5000.0, "profit_per_unit": 17.67, "margin": 9.23,
        "profit_per_m3": 1767.0, "decision": "Import",
    }])
    monkeypatch.setattr(storage, "latest_ore_snapshot", lambda: df)

    resp = client.get("/api/refining/shortlist/snapshot")

    assert resp.status_code == 200
    assert resp.json()[0]["item"] == "Compressed Veldspar"


def test_get_shortlist_snapshot_empty_returns_empty_list(monkeypatch):
    monkeypatch.setattr(storage, "latest_ore_snapshot", lambda: pd.DataFrame())
    resp = client.get("/api/refining/shortlist/snapshot")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_shortlist_items(monkeypatch):
    monkeypatch.setattr(storage, "load_ore_shortlist", lambda: [(34, "Compressed Veldspar", "Veldspar", False, True)])
    resp = client.get("/api/refining/shortlist/items")
    assert resp.status_code == 200
    assert resp.json() == [{"item_id": 34, "item": "Compressed Veldspar", "family": "Veldspar",
                             "is_ice": False, "active": True}]


def test_add_candidates_calls_action(monkeypatch):
    monkeypatch.setattr(refining_actions, "do_add_ore_to_shortlist", lambda: {"added": 3, "already_tracked": 12})
    resp = client.post("/api/refining/shortlist/add-candidates")
    assert resp.status_code == 200
    assert resp.json() == {"added": 3, "already_tracked": 12}


def test_refresh_shortlist_action_error_maps_to_400(monkeypatch):
    def _raise(**kwargs):
        raise ActionError("Ore Shortlist is empty - click 'Add Candidates' first.")
    monkeypatch.setattr(refining_actions, "do_refresh_ore_shortlist", _raise)

    resp = client.post("/api/refining/shortlist/refresh")

    assert resp.status_code == 400
    assert "Add Candidates" in resp.json()["detail"]


def test_update_settings_passes_body_to_action(monkeypatch):
    captured = {}

    def _update(updates):
        captured["updates"] = updates
        return updates
    monkeypatch.setattr(refining_actions, "do_update_settings", _update)

    body = {
        "structure_type": "Tatara (L Refinery)", "rig_tier": "T2-Rig", "security_status": -1.0,
        "implant": "RX-804", "reprocessing_skill_level": 5, "reprocessing_efficiency_skill_level": 5,
        "ore_family_skill_levels": {"Veldspar": 5}, "scrapmetal_processing_skill_level": 5,
        "refining_tax_rate": 0.0,
    }
    resp = client.post("/api/refining/settings", json=body)

    assert resp.status_code == 200
    assert captured["updates"]["structure_type"] == "Tatara (L Refinery)"


def test_settings_options_lists_real_constants():
    resp = client.get("/api/refining/settings/options")
    assert resp.status_code == 200
    body = resp.json()
    assert "Tatara (L Refinery)" in body["structure_types"]
    assert "T2-Rig" in body["rig_tiers"]
    assert "RX-804" in body["implants"]


def test_quote_reprocessing_passes_paste_to_action(monkeypatch):
    captured = {}

    def _quote(paste_text):
        captured["paste_text"] = paste_text
        return {"rows": [], "totals": {"reprocess_count": 0, "total_mineral_value": 0.0,
                                        "total_refined_value": 0.0, "total_sell_as_is_value": 0.0}}
    monkeypatch.setattr(refining_actions, "do_quote_reprocessing", _quote)

    resp = client.post("/api/refining/reprocessing/quote", json={"paste": "Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t"})

    assert resp.status_code == 200
    assert captured["paste_text"] == "Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t"
    assert resp.json() == {"rows": [], "totals": {"reprocess_count": 0, "total_mineral_value": 0.0,
                                                    "total_refined_value": 0.0, "total_sell_as_is_value": 0.0}}


def test_quote_reprocessing_action_error_maps_to_400(monkeypatch):
    def _raise(paste_text):
        raise ActionError("Paste is empty - copy items from an Inventory window's list view first.")
    monkeypatch.setattr(refining_actions, "do_quote_reprocessing", _raise)

    resp = client.post("/api/refining/reprocessing/quote", json={"paste": ""})

    assert resp.status_code == 400
    assert "Paste is empty" in resp.json()["detail"]

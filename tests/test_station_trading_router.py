"""Router-level tests for api/routers/station_trading.py. Same pattern as
test_refining_router.py: every do_* call and storage read is monkeypatched,
never touches the real DB/ESI."""
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.station_trading import actions as station_trading_actions

client = TestClient(create_app())


def test_get_shortlist_calls_action(monkeypatch):
    rows = [{"type_id": 34, "name": "Tritanium", "category": "Mineral", "spread_pct": 0.1,
             "avg_daily_volume": 5000.0, "discovered_at": "2026-08-27T00:00:00", "active": True,
             "live_buy": None, "live_sell": None, "profit_per_unit": None, "margin": None, "profit_per_day": None}]
    monkeypatch.setattr(station_trading_actions, "do_get_shortlist", lambda: rows)

    resp = client.get("/api/station-trading/shortlist")

    assert resp.status_code == 200
    assert resp.json() == rows


def test_get_trader_characters(monkeypatch):
    monkeypatch.setattr(station_trading_actions, "do_list_trader_characters",
                         lambda: [("trader:2112625428", 2112625428, "Some Character")])

    resp = client.get("/api/station-trading/trader-characters")

    assert resp.status_code == 200
    assert resp.json() == [{"role_key": "trader:2112625428", "character_id": 2112625428,
                             "character_name": "Some Character"}]


def test_get_skill_summary_calls_action(monkeypatch):
    summary = [{"character_name": "Some Character", "levels": {"Trade": 5}, "order_slots": 25, "error": None}]
    monkeypatch.setattr(station_trading_actions, "do_get_skill_summary", lambda: summary)

    resp = client.get("/api/station-trading/skills")

    assert resp.status_code == 200
    assert resp.json() == summary


def test_get_settings_returns_defaults():
    resp = client.get("/api/station-trading/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["station_id"] == 60003760


def test_get_esi_sync_time(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: "2026-08-27T00:00:00")
    resp = client.get("/api/station-trading/esi/sync-time")
    assert resp.status_code == 200
    assert resp.json() == {"synced_at": "2026-08-27T00:00:00"}


def test_refresh_shortlist_calls_action(monkeypatch):
    monkeypatch.setattr(station_trading_actions, "do_refresh_shortlist", lambda: {"discovered": 3, "rows": []})

    resp = client.post("/api/station-trading/shortlist/refresh")

    assert resp.status_code == 200
    assert resp.json() == {"discovered": 3, "rows": []}


def test_deactivate_shortlist_items_passes_type_ids_to_action(monkeypatch):
    captured = {}

    def _deactivate(type_ids):
        captured["type_ids"] = type_ids
        return {"deactivated": len(type_ids)}
    monkeypatch.setattr(station_trading_actions, "do_deactivate_shortlist_items", _deactivate)

    resp = client.post("/api/station-trading/shortlist/deactivate", json={"type_ids": [34, 35]})

    assert resp.status_code == 200
    assert captured["type_ids"] == [34, 35]
    assert resp.json() == {"deactivated": 2}


def test_check_undercut_calls_action(monkeypatch):
    result = {"sell": [], "buy": []}
    monkeypatch.setattr(station_trading_actions, "do_check_undercut", lambda: result)

    resp = client.post("/api/station-trading/undercut/check")

    assert resp.status_code == 200
    assert resp.json() == result


def test_check_undercut_action_error_maps_to_400(monkeypatch):
    def _raise():
        raise ActionError("No trader characters registered yet - add one first.")
    monkeypatch.setattr(station_trading_actions, "do_check_undercut", _raise)

    resp = client.post("/api/station-trading/undercut/check")

    assert resp.status_code == 400
    assert "No trader characters" in resp.json()["detail"]


def test_remove_trader_character_calls_action(monkeypatch):
    captured = {}

    def _remove(role_key):
        captured["role_key"] = role_key
        return {"removed": role_key}
    monkeypatch.setattr(station_trading_actions, "do_remove_trader_character", _remove)

    resp = client.delete("/api/station-trading/auth/character/trader:2112625428")

    assert resp.status_code == 200
    assert captured["role_key"] == "trader:2112625428"


def test_update_settings_passes_body_to_action(monkeypatch):
    captured = {}

    def _update(updates):
        captured["updates"] = updates
        return updates
    monkeypatch.setattr(station_trading_actions, "do_update_settings", _update)

    body = {"station_id": 60003760, "broker_fee_rate": 0.03, "sales_tax_rate": 0.05,
            "min_spread_threshold": 0.08, "min_daily_volume": 1.0,
            "enforce_shortlist_cap": False, "max_active_shortlist_items": 300}
    resp = client.post("/api/station-trading/settings", json=body)

    assert resp.status_code == 200
    assert captured["updates"]["station_id"] == 60003760

"""Router-level tests for /api/admin/* - see test_api_routers.py's own
docstring for the pattern this follows (every admin.do_* call monkeypatched,
never touches real Postgres/ESI). admin.py's own do_* logic has its own
Postgres-backed coverage in test_admin.py.
"""
from fastapi.testclient import TestClient

from eve_trader import admin
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app

client = TestClient(create_app())


def test_list_tenants_serializes_action_result(monkeypatch):
    monkeypatch.setattr(admin, "do_list_tenants", lambda: [
        {"tenant_id": "t1", "name": "Some Corp", "created_at": "2026-08-18T00:00:00"},
    ])

    resp = client.get("/api/admin/tenants")

    assert resp.status_code == 200
    assert resp.json() == [{"tenant_id": "t1", "name": "Some Corp", "created_at": "2026-08-18T00:00:00"}]


def test_list_users_serializes_action_result(monkeypatch):
    monkeypatch.setattr(admin, "do_list_users", lambda: [
        {"character_id": 1, "character_name": "Alice", "tenant_id": "t1",
         "tenant_name": "Some Corp", "tool_keys": ["trading"]},
    ])

    resp = client.get("/api/admin/users")

    assert resp.status_code == 200
    assert resp.json() == [{
        "character_id": 1, "character_name": "Alice", "tenant_id": "t1",
        "tenant_name": "Some Corp", "tool_keys": ["trading"],
    }]


def test_add_user_passes_body_fields_to_action(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"character_id": 42, "character_name": kwargs["character_name"], "tenant_id": "t1"}
    monkeypatch.setattr(admin, "do_add_user", _capture)

    resp = client.post("/api/admin/users", json={"character_name": "Some Pilot"})

    assert resp.status_code == 200
    assert captured == {"character_name": "Some Pilot"}


def test_add_user_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("No character found named 'Nobody Real'.")
    monkeypatch.setattr(admin, "do_add_user", _raise)

    resp = client.post("/api/admin/users", json={"character_name": "Nobody Real"})

    assert resp.status_code == 400
    assert resp.json() == {"detail": "No character found named 'Nobody Real'."}


def test_remove_user_passes_character_id(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"removed": kwargs["character_id"]}
    monkeypatch.setattr(admin, "do_remove_user", _capture)

    resp = client.delete("/api/admin/users/42")

    assert resp.status_code == 200
    assert captured == {"character_id": 42}


def test_set_tool_grants_passes_character_id_and_body(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"character_id": kwargs["character_id"], "tool_keys": kwargs["tool_keys"]}
    monkeypatch.setattr(admin, "do_set_tool_grants", _capture)

    resp = client.put("/api/admin/users/42/tools", json={"tool_keys": ["production", "admin"]})

    assert resp.status_code == 200
    assert captured == {"character_id": 42, "tool_keys": ["production", "admin"]}


def test_set_tool_grants_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("Unknown tool_key(s): bogus")
    monkeypatch.setattr(admin, "do_set_tool_grants", _raise)

    resp = client.put("/api/admin/users/42/tools", json={"tool_keys": ["bogus"]})

    assert resp.status_code == 400
    assert resp.json() == {"detail": "Unknown tool_key(s): bogus"}


def test_refresh_sde_serializes_action_result(monkeypatch):
    # GitHub issue #34: moved here from /api/production/sde/refresh.
    monkeypatch.setattr(admin, "do_refresh_sde", lambda: {"sde_types": 100})

    resp = client.post("/api/admin/sde/refresh")

    assert resp.status_code == 200
    assert resp.json() == {"sde_types": 100}


def test_refresh_sde_action_error_maps_to_400(monkeypatch):
    def _raise():
        raise ActionError("SDE refresh failed: connection refused")
    monkeypatch.setattr(admin, "do_refresh_sde", _raise)

    resp = client.post("/api/admin/sde/refresh")

    assert resp.status_code == 400
    assert resp.json() == {"detail": "SDE refresh failed: connection refused"}

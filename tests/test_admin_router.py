"""Router-level tests for /api/admin/* - see test_api_routers.py's own
docstring for the pattern this follows (every admin.do_* call monkeypatched,
never touches real Postgres/ESI). admin.py's own do_* logic has its own
Postgres-backed coverage in test_admin.py.
"""
from fastapi.testclient import TestClient

from eve_trader import admin, error_log
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
        "corporation_id": None, "corporation_name": None,
        "alliance_id": None, "alliance_name": None,
        "affiliation_checked_at": None, "access_suspended": False,
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


def test_preview_sde_starts_background_job(monkeypatch):
    monkeypatch.setattr(admin, "do_start_sde_preview", lambda: {
        "run_id": "sde-1", "status": "running", "job_name": "sde_preview", "tool": "admin",
    })

    resp = client.post("/api/admin/sde/preview")

    assert resp.status_code == 200
    assert resp.json() == {
        "run_id": "sde-1", "status": "running", "job_name": "sde_preview", "tool": "admin",
    }


def test_preview_sde_conflict_maps_to_409(monkeypatch):
    from eve_trader.actions import ConflictError

    def _raise():
        raise ConflictError("Sync Contracts is already running.")
    monkeypatch.setattr(admin, "do_start_sde_preview", _raise)

    resp = client.post("/api/admin/sde/preview")

    assert resp.status_code == 409
    assert resp.json() == {"detail": "Sync Contracts is already running."}


def test_preview_sde_status_returns_latest_run(monkeypatch):
    monkeypatch.setattr(admin, "do_sde_preview_status", lambda: {
        "run_id": "sde-1", "status": "running", "tool": "admin",
        "progress": {"phase": "run", "batch": 3, "total_batches": 13, "message": "Fetching invGroups.csv"},
    })
    resp = client.get("/api/admin/sde/preview/status")
    assert resp.status_code == 200
    assert resp.json()["progress"]["batch"] == 3
    assert resp.json()["progress"]["total_batches"] == 13


def test_preview_sde_action_error_maps_to_400(monkeypatch):
    def _raise():
        raise ActionError("SDE refresh failed: connection refused")
    monkeypatch.setattr(admin, "do_start_sde_preview", _raise)

    resp = client.post("/api/admin/sde/preview")

    assert resp.status_code == 400
    assert resp.json() == {"detail": "SDE refresh failed: connection refused"}


def test_apply_sde_passes_through_action(monkeypatch):
    monkeypatch.setattr(admin, "do_apply_sde", lambda: {"sde_types": 42})

    resp = client.post("/api/admin/sde/apply")

    assert resp.status_code == 200
    assert resp.json() == {"sde_types": 42}


def test_apply_sde_without_preview_maps_to_400(monkeypatch):
    def _raise():
        raise ActionError("Keine Preview-Daten vorhanden - bitte SDE-Update erneut prüfen.")
    monkeypatch.setattr(admin, "do_apply_sde", _raise)

    resp = client.post("/api/admin/sde/apply")

    assert resp.status_code == 400
    assert "Keine Preview-Daten" in resp.json()["detail"]


def test_refresh_jita_price_cache_action_error_maps_to_400(monkeypatch):
    def _raise():
        raise ActionError("Could not refresh Jita price cache (ESI down).")
    monkeypatch.setattr(admin, "do_refresh_jita_price_cache", _raise)

    resp = client.post("/api/admin/jita-price-cache/refresh")

    assert resp.status_code == 400
    assert "Jita price cache" in resp.json()["detail"]
    # GitHub issue #88 - error_log is its own module, not admin.py, since
    # its report endpoint (api/routers/errors.py) must stay reachable
    # without the "admin" tool grant every other route here requires - only
    # this GET (list) endpoint lives under /api/admin/*.
    monkeypatch.setattr(error_log, "do_list_errors", lambda limit: [
        {"id": 1, "tenant_id": "t1", "source": "frontend", "message": "boom",
         "detail": None, "path": "/trading", "created_at": "2026-08-18T00:00:00"},
    ])

    resp = client.get("/api/admin/errors")

    assert resp.status_code == 200
    assert resp.json() == [{
        "id": 1, "tenant_id": "t1", "source": "frontend", "message": "boom",
        "detail": None, "path": "/trading", "created_at": "2026-08-18T00:00:00",
    }]


def test_list_errors_passes_limit_query_param(monkeypatch):
    captured = {}

    def _capture(limit):
        captured["limit"] = limit
        return []
    monkeypatch.setattr(error_log, "do_list_errors", _capture)

    resp = client.get("/api/admin/errors?limit=50")

    assert resp.status_code == 200
    assert captured == {"limit": 50}


# Moved from test_api_routers.py (confirmed real misplacement 2026-09-21,
# see admin.do_create_backup's own docstring) - backups are now admin-only,
# not a Portfolio route.
def test_list_backups(monkeypatch):
    monkeypatch.setattr(admin, "do_list_backups", lambda: {"rows": [
        {"name": "eve_trader_backup_x.zip", "created_at": "2026-07-17T08:00:00+00:00", "size_bytes": 1234},
    ]})

    resp = client.get("/api/admin/backups")

    assert resp.status_code == 200
    assert resp.json()[0]["name"] == "eve_trader_backup_x.zip"


def test_create_backup(monkeypatch):
    monkeypatch.setattr(admin, "do_create_backup", lambda: {
        "name": "eve_trader_backup_x.zip", "created_at": "2026-07-17T08:00:00+00:00", "size_bytes": 1234,
    })

    resp = client.post("/api/admin/backups")

    assert resp.status_code == 200
    assert resp.json()["size_bytes"] == 1234


def test_create_backup_action_error_maps_to_400(monkeypatch):
    def boom():
        raise ActionError("Backup failed.")
    monkeypatch.setattr(admin, "do_create_backup", boom)

    resp = client.post("/api/admin/backups")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Backup failed."


# docs/MANUAL_TRACKING_PLAN.md phase 2, question 1
def test_get_structure_resolution_fallback(monkeypatch):
    monkeypatch.setattr(admin, "do_get_structure_resolution_fallback",
                         lambda: {"global_structure_resolution_fallback": True})

    resp = client.get("/api/admin/structures/fallback")

    assert resp.status_code == 200
    assert resp.json() == {"global_structure_resolution_fallback": True}


def test_set_structure_resolution_fallback_passes_body(monkeypatch):
    captured = {}

    def _set(enabled):
        captured["enabled"] = enabled
        return {"global_structure_resolution_fallback": enabled}
    monkeypatch.setattr(admin, "do_set_structure_resolution_fallback", _set)

    resp = client.put("/api/admin/structures/fallback", json={"enabled": True})

    assert resp.status_code == 200
    assert captured == {"enabled": True}
    assert resp.json() == {"global_structure_resolution_fallback": True}


# docs/MANUAL_TRACKING_PLAN.md phase 8
def test_start_structure_name_resolve_defaults_force_to_false(monkeypatch):
    captured = {}

    def _start(force):
        captured["force"] = force
        return {"run_id": "r1", "status": "running", "job_name": "structure_resolve", "tool": "admin"}
    monkeypatch.setattr(admin, "do_start_structure_name_resolve", _start)

    resp = client.post("/api/admin/structures/resolve", json={})

    assert resp.status_code == 200
    assert captured == {"force": False}
    assert resp.json()["run_id"] == "r1"


def test_start_structure_name_resolve_passes_force_true(monkeypatch):
    captured = {}
    monkeypatch.setattr(admin, "do_start_structure_name_resolve",
                         lambda force: captured.update(force=force) or {"run_id": "r2"})

    resp = client.post("/api/admin/structures/resolve", json={"force": True})

    assert resp.status_code == 200
    assert captured == {"force": True}


def test_start_structure_name_resolve_conflict_maps_to_409(monkeypatch):
    from eve_trader.actions import ConflictError
    monkeypatch.setattr(admin, "do_start_structure_name_resolve",
                         lambda force: (_ for _ in ()).throw(ConflictError("already running")))

    resp = client.post("/api/admin/structures/resolve", json={})

    assert resp.status_code == 409


def test_structure_resolve_status_serializes_action_result(monkeypatch):
    monkeypatch.setattr(admin, "do_structure_resolve_status", lambda: {
        "run_id": None, "job_name": "structure_resolve", "tool": "admin", "status": "idle",
        "progress": None, "result": None, "error": None,
    })

    resp = client.get("/api/admin/structures/resolve/status")

    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


# T3-08 (business-logic audit follow-up, 2026-09-26): the allowlist routes
# had zero coverage at this layer - test_admin_access_requests.py already
# covers admin.do_add_allowlist_entry/do_allowlist_impact's own logic
# directly (Postgres-backed), but nothing exercised the actual HTTP routes/
# request-schema wiring on top of them.
def test_list_allowlist_serializes_action_result(monkeypatch):
    monkeypatch.setattr(admin, "do_list_allowlist", lambda: [
        {"entry_type": "corporation", "entry_id": 500, "name": "Some Corp", "added_at": "2026-08-18T00:00:00"},
    ])

    resp = client.get("/api/admin/allowlist")

    assert resp.status_code == 200
    assert resp.json() == [
        {"entry_type": "corporation", "entry_id": 500, "name": "Some Corp", "added_at": "2026-08-18T00:00:00"},
    ]


def test_search_allowlist_forwards_query_param_as_name(monkeypatch):
    captured = {}
    monkeypatch.setattr(admin, "do_search_allowlist_candidates",
                         lambda name: captured.update(name=name) or [])

    resp = client.get("/api/admin/allowlist/search", params={"q": "Some Corp"})

    assert resp.status_code == 200
    assert captured == {"name": "Some Corp"}


def test_allowlist_impact_forwards_query_params(monkeypatch):
    captured = {}

    def _impact(entry_type, entry_id, action, actor_character_id):
        captured.update(entry_type=entry_type, entry_id=entry_id, action=action,
                         actor_character_id=actor_character_id)
        return {"affected": []}
    monkeypatch.setattr(admin, "do_allowlist_impact", _impact)

    resp = client.get("/api/admin/allowlist/impact",
                       params={"entry_type": "corporation", "entry_id": 500, "action": "add"})

    assert resp.status_code == 200
    assert captured == {"entry_type": "corporation", "entry_id": 500, "action": "add", "actor_character_id": None}


def test_add_allowlist_entry_forwards_body_fields(monkeypatch):
    captured = {}

    def _add(entry_type, entry_id, added_by_character_id):
        captured.update(entry_type=entry_type, entry_id=entry_id, added_by_character_id=added_by_character_id)
        return {"entry_type": entry_type, "entry_id": entry_id}
    monkeypatch.setattr(admin, "do_add_allowlist_entry", _add)

    resp = client.post("/api/admin/allowlist", json={"entry_type": "corporation", "entry_id": 500})

    assert resp.status_code == 200
    assert captured == {"entry_type": "corporation", "entry_id": 500, "added_by_character_id": None}


def test_add_allowlist_entry_action_error_maps_to_400(monkeypatch):
    def _raise(entry_type, entry_id, added_by_character_id):
        raise ActionError("Unknown entry_type.")
    monkeypatch.setattr(admin, "do_add_allowlist_entry", _raise)

    resp = client.post("/api/admin/allowlist", json={"entry_type": "bogus", "entry_id": 1})

    assert resp.status_code == 400


def test_remove_allowlist_entry_forwards_path_params(monkeypatch):
    captured = {}
    monkeypatch.setattr(admin, "do_remove_allowlist_entry",
                         lambda entry_type, entry_id: captured.update(entry_type=entry_type, entry_id=entry_id))

    resp = client.delete("/api/admin/allowlist/corporation/500")

    assert resp.status_code == 200
    assert captured == {"entry_type": "corporation", "entry_id": 500}

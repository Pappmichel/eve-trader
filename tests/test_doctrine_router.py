"""Router-level tests for api/routers/doctrine.py (GitHub issue #67, found
in a full-codebase audit 2026-08-21: this router had zero test coverage at
this layer - only exercised indirectly through doctrine/actions.py's own
unit tests, never through the actual FastAPI route/_wrap error-handling
path the way every sibling tool's router already is). Same pattern
test_api_routers.py already uses for trading/production/portfolio/admin -
every do_* call is monkeypatched, so these never touch the real DB, ESI, or
Goonmetrics. Not exhaustive over all 25 endpoints - one representative test
per distinct shape (list/create/update/delete/error-mapping/non-wrapped
read/characters/settings), since business logic itself is already covered
by doctrine/actions.py's own tests; this file only verifies the router's
own wiring (path/body reach the right actions.do_* kwargs, results
serialize, ActionError maps to 400)."""
from fastapi.testclient import TestClient

from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.doctrine import actions as doctrine_actions

client = TestClient(create_app())


# ------------------------------------------------------------------ doctrines
def test_list_doctrines_with_status(monkeypatch):
    monkeypatch.setattr(doctrine_actions, "do_get_doctrine_status", lambda doctrine_id=None: {"doctrines": [{
        "doctrine_id": "d1", "doctrine_name": "Rifter Fleet", "overall": "green",
        "contract_rollup": "green", "stockpile_rollup": "green", "fittings": [],
    }]})
    resp = client.get("/api/doctrine/doctrines")
    assert resp.status_code == 200
    assert resp.json()[0]["doctrine_name"] == "Rifter Fleet"


def test_create_doctrine_passes_body_to_action(monkeypatch):
    captured = {}

    def _create(name, description=None):
        captured["name"] = name
        captured["description"] = description
        return {"doctrine_id": "d1", "name": name, "description": description, "active": True, "created_at": None}
    monkeypatch.setattr(doctrine_actions, "do_create_doctrine", _create)

    resp = client.post("/api/doctrine/doctrines", json={"name": "Rifter Fleet", "description": "cheap tackle"})

    assert resp.status_code == 200
    assert captured == {"name": "Rifter Fleet", "description": "cheap tackle"}
    assert resp.json()["name"] == "Rifter Fleet"


def test_create_doctrine_action_error_maps_to_400(monkeypatch):
    def _raise(name, description=None):
        raise ActionError("A doctrine named 'Rifter Fleet' already exists.")
    monkeypatch.setattr(doctrine_actions, "do_create_doctrine", _raise)

    resp = client.post("/api/doctrine/doctrines", json={"name": "Rifter Fleet"})

    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_update_doctrine_passes_path_and_body_to_action(monkeypatch):
    captured = {}

    def _update(doctrine_id, name=None, description=None, active=None):
        captured.update(doctrine_id=doctrine_id, name=name, description=description, active=active)
        return {"doctrine_id": doctrine_id, "name": "Rifter Fleet", "description": description,
                "active": active, "created_at": None}
    monkeypatch.setattr(doctrine_actions, "do_update_doctrine", _update)

    resp = client.patch("/api/doctrine/doctrines/d1", json={"active": False})

    assert resp.status_code == 200
    assert captured == {"doctrine_id": "d1", "name": None, "description": None, "active": False}


def test_delete_doctrine(monkeypatch):
    captured = {}

    def _delete(doctrine_id):
        captured["doctrine_id"] = doctrine_id
        return {"deleted": doctrine_id}
    monkeypatch.setattr(doctrine_actions, "do_delete_doctrine", _delete)

    resp = client.delete("/api/doctrine/doctrines/d1")

    assert resp.status_code == 200
    assert captured["doctrine_id"] == "d1"
    assert resp.json() == {"deleted": "d1"}


# -------------------------------------------------------------------- fittings
def test_parse_fitting(monkeypatch):
    monkeypatch.setattr(doctrine_actions, "do_parse_fitting", lambda raw_eft: {
        "hull_type_id": 587, "hull_name": "Rifter", "fit_name": "Test Fit",
    })
    resp = client.post("/api/doctrine/fittings/parse", json={"raw_eft": "[Rifter, Test Fit]\n"})
    assert resp.status_code == 200
    assert resp.json()["hull_name"] == "Rifter"


def test_add_fitting_passes_doctrine_id_and_body(monkeypatch):
    captured = {}

    def _add(doctrine_id, **kwargs):
        captured["doctrine_id"] = doctrine_id
        captured.update(kwargs)
        return {"fitting_id": "f1"}
    monkeypatch.setattr(doctrine_actions, "do_add_fitting", _add)

    resp = client.post("/api/doctrine/doctrines/d1/fittings", json={"raw_eft": "[Rifter, Test]\n"})

    assert resp.status_code == 200
    assert captured["doctrine_id"] == "d1"
    assert captured["raw_eft"] == "[Rifter, Test]\n"


def test_get_fitting_detail_not_found_maps_to_400(monkeypatch):
    def _raise(fitting_id):
        raise ActionError(f"Fitting {fitting_id} not found.")
    monkeypatch.setattr(doctrine_actions, "do_get_fitting_detail", _raise)

    resp = client.get("/api/doctrine/fittings/missing")

    assert resp.status_code == 400


def test_delete_fitting(monkeypatch):
    monkeypatch.setattr(doctrine_actions, "do_delete_fitting", lambda fitting_id: {"deleted": fitting_id})
    resp = client.delete("/api/doctrine/fittings/f1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "f1"}


# ----------------------------------------------------------------------- sync
def test_sync_contracts(monkeypatch):
    monkeypatch.setattr(doctrine_actions, "do_sync_contracts", lambda: {"synced": 5})
    resp = client.post("/api/doctrine/sync")
    assert resp.status_code == 200
    assert resp.json() == {"synced": 5}


def test_get_sync_time_is_not_wrapped_but_still_reachable(monkeypatch):
    # GitHub issue #67's own audit note: get_sync_time/get_asset_sync_time
    # deliberately don't go through _wrap (matches trading/production's own
    # equivalent sync-time reads) - just confirm it's actually wired up.
    monkeypatch.setattr(doctrine_actions, "do_get_esi_sync_time", lambda: {"synced_at": "2026-08-21T00:00:00"})
    resp = client.get("/api/doctrine/sync-time")
    assert resp.status_code == 200
    assert resp.json() == {"synced_at": "2026-08-21T00:00:00"}


# --------------------------------------------------------------------- status
def test_get_status_passes_optional_doctrine_id_query_param(monkeypatch):
    captured = {}

    def _status(doctrine_id=None):
        captured["doctrine_id"] = doctrine_id
        return {"doctrines": []}
    monkeypatch.setattr(doctrine_actions, "do_get_doctrine_status", _status)

    resp = client.get("/api/doctrine/status?doctrine_id=d1")

    assert resp.status_code == 200
    assert captured["doctrine_id"] == "d1"


def test_list_contracts(monkeypatch):
    from eve_trader.doctrine.models import ContractRow
    monkeypatch.setattr(doctrine_actions, "do_list_contracts", lambda fitting_id=None, status=None: {"rows": [
        ContractRow(contract_id=1, source_role="doctrine:1", for_corporation=False, status="outstanding",
                    validation_status="valid", issuer_id=None, start_location_id=None, title=None, price=None,
                    date_expired=None, matched_fitting_id=None, match_score=None, synced_at=None,
                    source_character_name=None, hull_type_id=None),
    ]})
    resp = client.get("/api/doctrine/contracts")
    assert resp.status_code == 200
    assert resp.json()[0]["contract_id"] == 1


def test_get_stockpile(monkeypatch):
    monkeypatch.setattr(doctrine_actions, "do_get_stockpile_status", lambda doctrine_id=None: {"rows": []})
    resp = client.get("/api/doctrine/stockpile")
    assert resp.status_code == 200


# ------------------------------------------------------------------ characters
def test_get_doctrine_characters(monkeypatch):
    monkeypatch.setattr(doctrine_actions, "do_list_doctrine_characters",
                         lambda: [("doctrine:1", 1, "Some Character")])
    resp = client.get("/api/doctrine/characters")
    assert resp.status_code == 200
    assert resp.json() == [{"role_key": "doctrine:1", "character_id": 1, "character_name": "Some Character"}]


def test_remove_doctrine_character(monkeypatch):
    monkeypatch.setattr(doctrine_actions, "do_remove_doctrine_character", lambda role_key: {"removed": role_key})
    resp = client.delete("/api/doctrine/characters/doctrine:1")
    assert resp.status_code == 200
    assert resp.json() == {"removed": "doctrine:1"}


# ------------------------------------------------------------------- settings
def test_get_settings_reads_live_config():
    resp = client.get("/api/doctrine/settings")
    assert resp.status_code == 200
    assert "cargo_tolerance_pct" in resp.json()


def test_update_settings_action_error_maps_to_400(monkeypatch):
    def _raise(updates):
        raise ActionError("cargo_tolerance_pct: 5.0 is above the maximum allowed value (1)")
    monkeypatch.setattr(doctrine_actions, "do_update_settings", _raise)

    resp = client.post("/api/doctrine/settings", json={
        "cargo_tolerance_pct": 5.0, "strict_extras": False, "import_cost_per_m3": 900.0,
    })

    assert resp.status_code == 400
    assert "cargo_tolerance_pct" in resp.json()["detail"]

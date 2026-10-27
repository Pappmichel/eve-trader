"""Location resolution phases 5-7 (docs/LOCATION_RESOLUTION_PLAN.md): naming
and retrying one location, the Asset Manager routes for it, the one-time copy
out of structure_names (and its drop), and the readers that moved onto the
registry."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.asset_manager import actions as am_actions
from eve_trader.locations import actions as location_actions
from eve_trader.locations import reader, resolver, store
from eve_trader.realized import db as realized_db

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

STRUCT = 1049865184771
SYSTEM, REGION = 30099771, 10099771
SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "location_registry_schema.sql"


@pytest.fixture
def sde(_apply_esi_access_schema):
    pg_helpers.wipe_tables("global_structure_names")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO sde_regions VALUES (%s,%s) ON CONFLICT DO NOTHING", (REGION, "Region 771"))
        conn.execute("INSERT INTO sde_solar_systems VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                     (SYSTEM, "System 771", 0.4, REGION))


# ------------------------------------------------------------------ naming
@pg_helpers.postgres_required()
def test_a_manual_name_labels_an_unresolved_structure(tenant, sde):
    location_actions.do_set_manual_name(STRUCT, "  My Fort  ")

    assert storage.get_location_names([STRUCT]) == {STRUCT: "My Fort"}
    location_actions.do_remove_manual_name(STRUCT)
    assert storage.get_location_names([STRUCT]) == {STRUCT: None}


@pytest.mark.parametrize("name, location_id, match", [
    ("", STRUCT, "empty"), ("x" * 101, STRUCT, "at most"), ("Fort", 0, "Unknown location"),
])
def test_manual_name_is_validated(name, location_id, match):
    with pytest.raises(ActionError, match=match):
        location_actions.do_set_manual_name(location_id, name)


def test_retry_forces_a_lookup_of_that_id(monkeypatch):
    captured = {}

    def fake(ids, **kw):
        captured.update(ids=ids, **kw)
        return {"ids": {STRUCT: {"name": "Fort", "status": "resolved", "attempted": True}}}
    monkeypatch.setattr(resolver, "resolve_ids", fake)

    assert location_actions.do_retry_lookup(STRUCT) == {"location_id": STRUCT, "name": "Fort", "status": "resolved"}
    assert captured["ids"] == [STRUCT] and captured["force"] is True


def test_retry_without_any_capable_character_says_so(monkeypatch):
    monkeypatch.setattr(resolver, "resolve_ids", lambda ids, **kw: {
        "skipped": "no character has Structure name resolution ticked",
        "ids": {STRUCT: {"name": None, "status": "pending", "attempted": False}}})

    with pytest.raises(ActionError, match="Structure name resolution"):
        location_actions.do_retry_lookup(STRUCT)


# ------------------------------------------------------------------ Asset Manager routes
client = TestClient(create_app())


def test_asset_manager_location_routes_reach_the_actions(monkeypatch):
    calls = []
    monkeypatch.setattr(am_actions, "do_set_location_name", lambda **kw: calls.append(("set", kw)) or kw)
    monkeypatch.setattr(am_actions, "do_remove_location_name", lambda **kw: calls.append(("remove", kw)) or kw)
    monkeypatch.setattr(am_actions, "do_retry_location", lambda **kw: calls.append(("retry", kw)) or kw)

    assert client.put(f"/api/asset-manager/locations/{STRUCT}/name", json={"name": "Fort"}).status_code == 200
    assert client.delete(f"/api/asset-manager/locations/{STRUCT}/name").status_code == 200
    assert client.post(f"/api/asset-manager/locations/{STRUCT}/retry").status_code == 200
    assert calls == [("set", {"location_id": STRUCT, "name": "Fort"}), ("remove", {"location_id": STRUCT}),
                     ("retry", {"location_id": STRUCT})]


def test_asset_manager_location_name_error_is_400(monkeypatch):
    def boom(**kw):
        raise ActionError("Name must not be empty.")
    monkeypatch.setattr(am_actions, "do_set_location_name", boom)

    resp = client.put(f"/api/asset-manager/locations/{STRUCT}/name", json={"name": ""})

    assert resp.status_code == 400 and "empty" in resp.json()["detail"]


# ------------------------------------------------------------------ phase 7: registry is the source
@pg_helpers.postgres_required()
def test_schema_copies_old_structure_names_into_the_registry_and_drops_them(tenant, sde):
    # The shape structure_names had on a deployment before phase 7.
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("CREATE TABLE structure_names (tenant_id UUID NOT NULL, location_id BIGINT NOT NULL, "
                     "name TEXT, solar_system_id INTEGER, PRIMARY KEY (tenant_id, location_id))")
        conn.execute("INSERT INTO structure_names VALUES (%s, %s, %s, %s), (%s, %s, NULL, NULL)",
                     (tenant, STRUCT, "Old Fort", SYSTEM, tenant, STRUCT + 1))
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        assert conn.execute("SELECT to_regclass('public.structure_names')").fetchone()[0] is None
        conn.execute(SCHEMA.read_text(encoding="utf-8"))  # safe to re-run once the table is gone

    rows = store.load([STRUCT, STRUCT + 1])
    assert rows[STRUCT]["name"] == "Old Fort" and rows[STRUCT]["status"] == "resolved"
    assert rows[STRUCT]["solar_system_id"] == SYSTEM
    assert STRUCT + 1 not in rows  # a failed lookup is not copied; the resolver registers it again
    assert storage.get_location_names([STRUCT]) == {STRUCT: "Old Fort"}


@pg_helpers.postgres_required()
def test_legacy_writer_and_readers_use_the_registry(tenant, sde):
    storage.set_cached_structure_name(STRUCT, "Fort", SYSTEM)

    assert storage.get_cached_structure_name(STRUCT) == (True, "Fort")
    assert (STRUCT, "Fort") in storage.list_cached_structure_names()
    assert (STRUCT, SYSTEM) in storage.list_structure_system_ids()
    assert storage.get_structure_system_id(STRUCT) == SYSTEM
    assert [r[0] for r in storage.search_locations("Fort")] == [STRUCT]
    storage.set_cached_structure_name(STRUCT, None)
    assert storage.get_cached_structure_name(STRUCT) == (True, None)


@pg_helpers.postgres_required()
def test_a_registered_but_never_looked_up_id_is_not_cached(tenant, sde):
    store.register([{"location_id": STRUCT, "kind": "structure"}])

    assert storage.get_cached_structure_name(STRUCT) == (False, None)
    assert STRUCT not in {r[0] for r in storage.list_cached_structure_names()}


@pg_helpers.postgres_required()
def test_realized_region_comes_from_the_central_reader(tenant, sde):
    storage.set_cached_structure_name(STRUCT, "Fort", SYSTEM)

    assert realized_db.region_id_for_location(STRUCT) == REGION
    assert realized_db.region_id_for_location(1049865184772) is None
    assert reader.describe([STRUCT])[STRUCT].region_name == "Region 771"

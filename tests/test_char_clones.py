"""Character Management phase 5c: clones and implants (storage, fetchers, stale
clear, fail-closed accessor, Character Info detail)."""
from __future__ import annotations

import datetime as dt

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError  # noqa: F401
from eve_trader.character_management import info_actions
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.access import read_esi
from eve_trader.esi_data.orchestrator import do_sync_for_tool
from eve_trader.esi_data.stale import clear_stale_owner_kind

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant, tenant_pair,
)
from .test_char_info import ALICE, BOB, FakeClient, _share, _token, fake  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

SCOPES = "esi-clones.read_clones.v1 esi-clones.read_implants.v1"
_TABLES = (
    "character_clone_meta", "character_jump_clones", "character_jump_clone_implants",
    "character_implants", "esi_sharing", "esi_freshness", "tenant_tokens",
)

RAW_CLONES = {
    "home_location": {"location_id": 60003760, "location_type": "station"},
    "jump_clones": [
        {"jump_clone_id": 7, "location_id": 60008494, "location_type": "station", "implants": [9899, 9899, 9941]},
        {"jump_clone_id": 8, "location_id": 1035466617946, "location_type": "structure", "implants": []},
    ],
    "last_clone_jump_date": "2026-09-28T10:00:00Z",
    "last_station_change_date": "2026-09-01T00:00:00Z",
}


class ClonesClient(FakeClient):
    def character_clones(self, cid, auth_role):
        self._rec("clones", cid)
        return RAW_CLONES

    def character_implants(self, cid, auth_role):
        self._rec("implants", cid)
        return [9941, 9899]


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES)
    orchestrator._in_flight.clear()
    yield
    pg_helpers.wipe_tables(*_TABLES)


def _sync(client=None):
    return do_sync_for_tool("char_info", client=client or ClonesClient())


def test_replace_and_load_clones_round_trip_and_replace_per_character(tenant):
    meta = {"home_location_id": 5, "home_location_type": "station",
            "last_clone_jump_date": "2026-09-28T10:00:00Z", "last_station_change_date": None}
    storage.replace_character_clones(ALICE, meta, [
        {"jump_clone_id": 2, "location_id": 9, "location_type": "structure", "name": "Mining", "implants": [3, 3, 1]},
        {"jump_clone_id": 1, "location_id": 8, "location_type": "station", "name": None, "implants": []},
    ])
    storage.replace_character_clones(BOB, {}, [])
    got = storage.load_character_clones([ALICE, BOB])
    assert [c["jump_clone_id"] for c in got[ALICE]["jump_clones"]] == [1, 2]
    assert got[ALICE]["jump_clones"][1]["implants"] == [1, 3]           # de-duplicated, sorted
    assert got[ALICE]["meta"]["home_location_id"] == 5
    assert got[BOB] == {"meta": {"home_location_id": None, "home_location_type": None,
                                 "last_clone_jump_date": None, "last_station_change_date": None},
                        "jump_clones": []}                              # meta row marks "synced, nothing there"
    storage.replace_character_clones(ALICE, {}, [])                    # a re-sync replaces, never appends
    assert storage.load_character_clones([ALICE])[ALICE]["jump_clones"] == []
    assert storage.load_character_clones([]) == {}


def test_implants_are_replaced_and_empty_clears(tenant):
    storage.replace_character_implants(ALICE, [3, 1, 3])
    assert storage.load_character_implants([ALICE]) == [(ALICE, 1), (ALICE, 3)]
    storage.replace_character_implants(ALICE, [])
    assert storage.load_character_implants([ALICE]) == []
    assert storage.load_character_implants([]) == []


def test_clone_rows_are_tenant_isolated(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        storage.replace_character_clones(ALICE, {"home_location_id": 1}, [])
        storage.replace_character_implants(ALICE, [1])
    with storage.tenant_context(b):
        assert storage.load_character_clones([ALICE]) == {}
        assert storage.load_character_implants([ALICE]) == []


def test_orchestrator_syncs_both_kinds(tenant):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "clones")
    _share(ALICE, "implants")
    client = ClonesClient()
    assert _sync(client)["ok"] is True
    assert {w for w, _ in client.calls} == {"clones", "implants"}
    got = storage.load_character_clones([ALICE])[ALICE]
    assert got["meta"]["home_location_id"] == 60003760
    assert [c["implants"] for c in got["jump_clones"]] == [[9899, 9941], []]
    assert storage.load_character_implants([ALICE]) == [(ALICE, 9899), (ALICE, 9941)]


def test_sync_reports_reauth_for_the_kind_whose_scope_is_missing(tenant):
    _token(ALICE, "Alice", scopes="esi-clones.read_implants.v1")   # no read_clones
    _share(ALICE, "clones")
    _share(ALICE, "implants")
    kinds = _sync()["owners"][0]["kinds"]
    assert kinds["clones"].startswith("re-auth needed")
    assert storage.load_character_implants([ALICE]) == [(ALICE, 9899), (ALICE, 9941)]
    assert storage.load_character_clones([ALICE]) == {}


def test_read_esi_is_fail_closed_and_returns_meta_with_clones(tenant):
    storage.replace_character_clones(ALICE, {"home_location_id": 5}, [])
    storage.replace_character_implants(ALICE, [1])
    assert read_esi("clones", "char_info") == []                        # not shared
    assert read_esi("implants", "char_info") == []
    _share(ALICE, "clones")
    _share(ALICE, "implants")
    (row,) = read_esi("clones", "char_info")
    assert row["owner_id"] == ALICE and row["meta"]["home_location_id"] == 5
    assert read_esi("implants", "char_info") == [{"owner_type": "character", "owner_id": ALICE, "type_id": 1}]


def test_stale_clear_removes_every_clone_table(tenant):
    storage.replace_character_clones(ALICE, {"home_location_id": 5}, [
        {"jump_clone_id": 1, "location_id": 8, "location_type": "station", "implants": [4]}])
    storage.replace_character_implants(ALICE, [1])
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=100)
    for kind in ("clones", "implants"):
        storage.upsert_esi_freshness("character", ALICE, kind, success=True)
        with storage.connect() as conn:
            conn.execute(
                "UPDATE esi_freshness SET last_success_at = ? WHERE owner_id = ? AND data_kind = ?",
                (old, ALICE, kind),
            )
        assert clear_stale_owner_kind(
            "character", ALICE, kind, tier_interval_hours=24, stale_clear_multiples=3,
        ) is True
    assert storage.load_character_clones([ALICE]) == {}
    assert storage.load_character_implants([ALICE]) == []
    with storage.connect() as conn:
        assert conn.execute("SELECT count(*) FROM character_jump_clone_implants").fetchone()[0] == 0


def test_detail_shows_names_home_and_jump_clones(tenant, fake, monkeypatch):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "clones")
    _share(ALICE, "implants")
    _sync()
    monkeypatch.setattr(storage, "get_location_names", lambda ids: {60003760: "Jita IV - Moon 4", 1035466617946: "C-J Keepstar"})
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda ids: {i: (i, 0, f"Implant {i}") for i in ids})
    detail = info_actions.do_character_detail(ALICE)
    clones = detail["clones"]["value"]
    assert clones["home"]["location_name"] == "Jita IV - Moon 4"
    assert [c["location_name"] for c in clones["jump_clones"]] == [None, "C-J Keepstar"]   # unresolved stays None
    assert [i["name"] for i in clones["jump_clones"][0]["implants"]] == ["Implant 9899", "Implant 9941"]
    assert clones["last_clone_jump_date"] == "2026-09-28T10:00:00+00:00" or "2026-09-28" in str(clones["last_clone_jump_date"])
    assert [i["type_id"] for i in detail["implants"]["value"]] == [9899, 9941]


def test_detail_fields_are_gated_like_every_other_kind(tenant, fake):
    _token(ALICE, "Alice", scopes="")
    detail = info_actions.do_character_detail(ALICE)
    assert detail["clones"]["state"] == "not_shared" and detail["implants"]["state"] == "not_shared"
    _share(ALICE, "clones")
    _share(ALICE, "implants")
    detail = info_actions.do_character_detail(ALICE)
    assert detail["clones"]["state"] == "reauth_needed"
    _token(ALICE, "Alice", scopes=SCOPES)
    detail = info_actions.do_character_detail(ALICE)
    assert detail["clones"]["state"] == "not_synced" and detail["implants"]["state"] == "not_synced"

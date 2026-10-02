"""Character Management phase 7: jump fatigue (live-only) and the clone jump
timer (derived from the clones snapshot)."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.character_management import info_actions
from eve_trader.esi_client import ESIClient, ESIError
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.access import AccessorError, read_esi
from eve_trader.esi_data.orchestrator import do_sync_for_tool

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant,
)
from .test_char_info import ALICE, BOB, FakeClient, _share, _token, fake  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

SCOPES = "esi-characters.read_fatigue.v1 esi-clones.read_clones.v1"
_TABLES = ("character_clone_meta", "character_jump_clones", "character_jump_clone_implants",
           "esi_sharing", "esi_freshness", "tenant_tokens")


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES)
    orchestrator._in_flight.clear()
    yield
    pg_helpers.wipe_tables(*_TABLES)


class FatigueClient(FakeClient):
    def __init__(self, result=None):
        super().__init__()
        self.result = {"jump_fatigue_expire_date": "2026-09-30T12:00:00Z", "last_jump_date": "2026-09-29T09:00:00Z",
                       "last_update_date": "2026-09-29T09:00:00Z"} if result is None else result

    def character_fatigue(self, cid, auth_role):
        self._rec("fatigue", cid)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def fatigue(monkeypatch, fake):
    client = FatigueClient()
    monkeypatch.setattr(info_actions, "ESIClient", lambda tokens=None: client)
    return client


def test_fatigue_is_a_live_kind_with_no_snapshot(tenant):
    _share(ALICE, "fatigue")
    with pytest.raises(AccessorError, match="live-only"):
        read_esi("fatigue", "char_info")
    _token(ALICE, "Alice", scopes=SCOPES)
    client = FatigueClient()
    do_sync_for_tool("char_info", client=client)
    assert client.calls == []                       # the orchestrator never fetches a live kind


def test_overview_reads_fatigue_only_when_shared_and_scoped(tenant, fatigue):
    _token(ALICE, "Alice", scopes="")
    alice = info_actions.do_list_character_overview()["characters"][0]
    assert alice["fatigue"]["state"] == "not_shared" and fatigue.calls == []
    _share(ALICE, "fatigue")
    assert info_actions.do_list_character_overview()["characters"][0]["fatigue"]["state"] == "reauth_needed"
    assert fatigue.calls == []
    _token(ALICE, "Alice", scopes=SCOPES)
    alice = info_actions.do_list_character_overview()["characters"][0]
    assert alice["fatigue"] == {"state": "ok", "value": {
        "jump_fatigue_expire_date": "2026-09-30T12:00:00Z", "last_jump_date": "2026-09-29T09:00:00Z",
        "last_update_date": "2026-09-29T09:00:00Z"}}
    assert ("fatigue", ALICE) in fatigue.calls and ("fatigue", BOB) not in fatigue.calls


def test_a_character_without_fatigue_gets_empty_dates_and_a_failure_stays_local(tenant, fatigue):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "fatigue")
    fatigue.result = {}
    assert info_actions.do_list_character_overview()["characters"][0]["fatigue"]["value"] == {
        "jump_fatigue_expire_date": None, "last_jump_date": None, "last_update_date": None}
    fatigue.result = ESIError("HTTP 500 for fatigue")
    row = info_actions.do_list_character_overview()["characters"][0]
    assert row["fatigue"]["state"] == "error" and row["online"]["state"] == "not_shared"


def test_esi_client_reads_the_fatigue_endpoint_through_the_live_cache(tenant, monkeypatch):
    seen = []
    client = ESIClient.__new__(ESIClient)
    monkeypatch.setattr(client, "_get", lambda path, params=None, auth_role=None: seen.append((path, params)) or {})
    client.character_fatigue(ALICE, "esi:1")
    client.character_fatigue(ALICE, "esi:1")
    assert seen == [(f"/characters/{ALICE}/fatigue/", {"datasource": "tranquility"})]      # second call cached


@pytest.mark.parametrize("last_jump,expected", [
    ("2026-09-28T10:00:00Z", "2026-09-29T10:00:00+00:00"),
    (None, None),
])
def test_clone_jump_availability_is_last_jump_plus_24_hours(tenant, last_jump, expected):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "clones")
    storage.replace_character_clones(ALICE, {"last_clone_jump_date": last_jump}, [])
    storage.upsert_esi_freshness("character", ALICE, "clones", success=True)
    from unittest import mock
    with mock.patch.object(info_actions.esi_actions, "do_list_token_characters",
                           lambda: [{"character_id": ALICE, "character_name": "Alice", "corporation_id": 1,
                                     "corporation_name": "C"}]), \
         mock.patch.object(info_actions, "ESIClient", lambda tokens=None: FatigueClient()):
        detail = info_actions.do_character_detail(ALICE)
    assert detail["clones"]["value"]["clone_jump_available_at"] == expected

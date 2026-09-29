"""Character Management phase 1 (docs/CHARACTER_MANAGEMENT_PLAN.md):
standings/loyalty snapshots, live-only kinds, the accessor gates, the
orchestrator's live_only skip, and the Character Info actions + router."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.auth import TokenRecord
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG
from eve_trader.esi_client import ESIError
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.access import AccessorError, is_shared, read_esi
from eve_trader.esi_data.orchestrator import do_sync_for_tool
from eve_trader.esi_data.stale import clear_stale_owner_kind
from eve_trader.character_management import info_actions
from eve_trader.actions import ActionError

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
BOB = 1002
SCOPE = {
    "standings": "esi-characters.read_standings.v1",
    "loyalty": "esi-characters.read_loyalty.v1",
    "location": "esi-location.read_location.v1",
    "ship": "esi-location.read_ship_type.v1",
    "online": "esi-location.read_online.v1",
    "wallet_balance": "esi-wallet.read_character_wallet.v1",
}
ALL_SCOPES = " ".join(SCOPE.values())

_TABLES = (
    "character_standings", "character_loyalty_points", "character_wallet_balances",
    "esi_sharing", "esi_freshness", "tenant_tokens",
)


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES)
    orchestrator._in_flight.clear()
    yield
    pg_helpers.wipe_tables(*_TABLES)


def _share(owner_id, kind, tool="char_info"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
            (owner_id, kind, tool),
        )


def _token(cid, name, scopes=ALL_SCOPES):
    role = f"esi:{cid}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=cid, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=scopes,
    )))


class FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []
        self.fail_location_for: set[int] = set()

    def _rec(self, what, cid):
        self.calls.append((what, cid))

    def character_public_info(self, cid):
        return {"name": f"Char{cid}", "corporation_id": 98000001, "alliance_id": 99000001,
                "security_status": 1.5, "birthday": "2010-01-01T00:00:00Z"}

    def corporation_public_info(self, corp_id):
        return {"name": f"Corp{corp_id}"}

    def resolve_names(self, ids):
        return {i: f"Name{i}" for i in ids}

    def character_location(self, cid, auth_role):
        self._rec("location", cid)
        if cid in self.fail_location_for:
            raise ESIError("HTTP 403 for location")
        return {"solar_system_id": 30000142, "station_id": 60003760}

    def character_ship(self, cid, auth_role):
        self._rec("ship", cid)
        return {"ship_type_id": 587, "ship_item_id": 1, "ship_name": "Rifty"}

    def character_online(self, cid, auth_role):
        self._rec("online", cid)
        return {"online": True, "last_login": "2026-09-29T08:00:00Z", "logins": 3}

    def character_standings(self, cid, auth_role):
        self._rec("standings", cid)
        return [{"from_id": 500001, "from_type": "faction", "standing": 5.5},
                {"from_id": 1000035, "from_type": "npc_corp", "standing": -2.0}]

    def character_loyalty_points(self, cid, auth_role):
        self._rec("loyalty", cid)
        return [{"corporation_id": 1000035, "loyalty_points": 12345}]

    def character_wallet_balance(self, cid, auth_role):
        self._rec("wallet_balance", cid)
        return 1_000_000.5

    def character_corporation_history(self, cid):
        return [{"corporation_id": 98000001, "start_date": "2020-01-01T00:00:00Z"},
                {"corporation_id": 1000009, "start_date": "2010-01-01T00:00:00Z"}]


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(info_actions, "ESIClient", lambda tokens=None: client)
    monkeypatch.setattr(info_actions.esi_actions, "do_list_token_characters", lambda: [
        {"character_id": ALICE, "character_name": "Alice", "corporation_id": 98000001,
         "corporation_name": "Corp98000001"},
        {"character_id": BOB, "character_name": "Bob", "corporation_id": 98000001,
         "corporation_name": "Corp98000001"},
    ])
    return client


# ------------------------------------------------ storage / fetchers / stale
def test_standings_and_loyalty_are_replaced_per_character(tenant):
    storage.replace_character_standings(ALICE, [(1, "faction", 1.0), (2, "agent", 2.0)])
    storage.replace_character_standings(BOB, [(3, "faction", 3.0)])
    storage.replace_character_standings(ALICE, [(9, "faction", 9.0)])
    rows = storage.load_character_standings([ALICE, BOB])
    assert sorted((r[0], r[1]) for r in rows) == [(ALICE, 9), (BOB, 3)]
    assert storage.load_character_standings([]) == []  # never unfiltered

    storage.replace_character_loyalty_points(ALICE, [(10, 500), (11, 700)])
    storage.replace_character_loyalty_points(ALICE, [])
    assert storage.load_character_loyalty_points([ALICE]) == []


def test_standings_rows_are_tenant_isolated(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        storage.replace_character_standings(ALICE, [(1, "faction", 1.0)])
    with storage.tenant_context(b):
        assert storage.load_character_standings([ALICE]) == []
        storage.replace_character_standings(ALICE, [(1, "faction", -1.0)])
    with storage.tenant_context(a):
        assert storage.load_character_standings([ALICE])[0][3] == 1.0


def test_stale_clear_removes_standings_and_loyalty_after_the_grace_window(tenant):
    storage.replace_character_standings(ALICE, [(1, "faction", 1.0)])
    storage.replace_character_loyalty_points(ALICE, [(10, 500)])
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=100)
    for kind in ("standings", "loyalty"):
        storage.upsert_esi_freshness("character", ALICE, kind, success=True)
        with storage.connect() as conn:
            conn.execute(
                "UPDATE esi_freshness SET last_success_at = ? WHERE owner_id = ? AND data_kind = ?",
                (old, ALICE, kind),
            )
        assert clear_stale_owner_kind(
            "character", ALICE, kind, tier_interval_hours=24, stale_clear_multiples=3,
        ) is True
    assert storage.load_character_standings([ALICE]) == []
    assert storage.load_character_loyalty_points([ALICE]) == []


# ------------------------------------------------------------------ accessor
def test_read_esi_is_fail_closed_for_the_new_snapshot_kinds(tenant):
    storage.replace_character_standings(ALICE, [(1, "faction", 1.0)])
    storage.replace_character_loyalty_points(ALICE, [(10, 500)])
    storage.upsert_character_wallet_balance(ALICE, 42.0)
    for kind in ("standings", "loyalty", "wallet_balance"):
        assert read_esi(kind, "char_info") == []           # nothing shared yet
        _share(ALICE, kind)
    assert read_esi("standings", "char_info")[0]["standing"] == 1.0
    assert read_esi("loyalty", "char_info")[0]["loyalty_points"] == 500
    assert read_esi("wallet_balance", "char_info")[0]["balance"] == 42.0
    # Sharing with char_info is not sharing with another tool.
    assert read_esi("standings", "portfolio") == []
    with pytest.raises(AccessorError):
        read_esi("standings", "")


def test_live_only_kinds_have_no_snapshot_to_read(tenant):
    _share(ALICE, "location")
    assert is_shared("location", "char_info", "character", ALICE) is True
    for kind in ("location", "ship", "online"):
        with pytest.raises(AccessorError, match="live-only"):
            read_esi(kind, "char_info")


# -------------------------------------------------------------- orchestrator
def test_orchestrator_syncs_snapshot_kinds_and_ignores_live_only_rows(tenant):
    _token(ALICE, "Alice")
    for kind in ("standings", "loyalty", "wallet_balance", "location", "ship", "online"):
        _share(ALICE, kind)
    client = FakeClient()
    result = do_sync_for_tool("char_info", client=client)
    assert result["ok"] is True
    called = {what for what, _ in client.calls}
    assert called == {"standings", "loyalty", "wallet_balance"}   # no live kinds
    assert storage.load_character_standings([ALICE])
    assert storage.load_character_loyalty_points([ALICE]) == [(ALICE, 1000035, 12345)]
    assert storage.load_character_wallet_balance(ALICE) == 1_000_000.5


def test_a_character_sharing_only_live_kinds_creates_no_owner_task(tenant):
    _token(ALICE, "Alice")
    _share(ALICE, "location")
    result = do_sync_for_tool("char_info", client=FakeClient())
    assert result["owners"] == []
    assert result["null_id_sweep"] is None


def test_sync_reports_reauth_when_no_token_holds_the_scope(tenant):
    _token(ALICE, "Alice", scopes=SCOPE["standings"])   # no loyalty scope
    _share(ALICE, "standings")
    _share(ALICE, "loyalty")
    result = do_sync_for_tool("char_info", client=FakeClient())
    kinds = result["owners"][0]["kinds"]
    assert kinds["loyalty"].startswith("re-auth needed")


# ------------------------------------------------------------ info actions
def test_overview_never_fetches_what_is_not_shared(tenant, fake):
    _token(ALICE, "Alice")
    result = info_actions.do_list_character_overview()
    alice = next(c for c in result["characters"] if c["character_id"] == ALICE)
    for field in ("location", "ship", "online", "wallet_balance"):
        assert alice[field]["state"] == "not_shared"
    assert fake.calls == []      # nothing live was fetched


def test_overview_live_fields_when_shared(tenant, fake):
    _token(ALICE, "Alice")
    for kind in ("location", "ship", "online"):
        _share(ALICE, kind)
    alice = info_actions.do_list_character_overview()["characters"][0]
    assert alice["location"]["state"] == "ok"
    assert alice["location"]["value"]["solar_system_id"] == 30000142
    assert alice["location"]["value"]["location_kind"] == "station"
    assert alice["ship"]["value"]["ship_name"] == "Rifty"
    assert alice["online"]["value"]["online"] is True
    assert alice["alliance_name"] == "Name99000001"
    assert alice["security_status"] == 1.5
    # Bob has a row too, but shares nothing.
    bob = next(c for c in info_actions.do_list_character_overview()["characters"]
               if c["character_id"] == BOB)
    assert bob["location"]["state"] == "not_shared"


def test_reauth_needed_when_shared_but_no_token_has_the_scope(tenant, fake):
    _token(ALICE, "Alice", scopes=SCOPE["online"])   # location scope missing
    _share(ALICE, "location")
    alice = info_actions.do_list_character_overview()["characters"][0]
    assert alice["location"]["state"] == "reauth_needed"
    assert ("location", ALICE) not in fake.calls


def test_one_failing_field_does_not_break_the_page_or_other_characters(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    for cid in (ALICE, BOB):
        _share(cid, "location")
        _share(cid, "online")
    fake.fail_location_for.add(ALICE)
    rows = {c["character_id"]: c for c in info_actions.do_list_character_overview()["characters"]}
    assert rows[ALICE]["location"]["state"] == "error"
    assert "403" in rows[ALICE]["location"]["detail"]
    assert rows[ALICE]["online"]["state"] == "ok"
    assert rows[BOB]["location"]["state"] == "ok"


def test_snapshot_field_states_not_synced_then_ok(tenant, fake):
    _token(ALICE, "Alice")
    _share(ALICE, "wallet_balance")
    row = info_actions.do_list_character_overview()["characters"][0]
    assert row["wallet_balance"]["state"] == "not_synced"
    storage.upsert_character_wallet_balance(ALICE, 250.0)
    storage.upsert_esi_freshness("character", ALICE, "wallet_balance", success=True)
    row = info_actions.do_list_character_overview()["characters"][0]
    assert row["wallet_balance"]["state"] == "ok"
    assert row["wallet_balance"]["value"] == 250.0


def test_detail_resolves_names_and_orders_history(tenant, fake):
    _token(ALICE, "Alice")
    _share(ALICE, "standings")
    _share(ALICE, "loyalty")
    do_sync_for_tool("char_info", client=fake)
    detail = info_actions.do_character_detail(ALICE)
    standings = detail["standings"]["value"]
    assert standings["faction"][0] == {"from_id": 500001, "name": "Name500001", "standing": 5.5}
    assert standings["npc_corp"][0]["standing"] == -2.0
    assert detail["loyalty_points"]["value"][0]["loyalty_points"] == 12345
    assert [h["corporation_id"] for h in detail["corporation_history"]] == [98000001, 1000009]


def test_detail_rejects_an_unregistered_character(tenant, fake):
    with pytest.raises(ActionError, match="not registered"):
        info_actions.do_character_detail(424242)
    with pytest.raises(ActionError):
        info_actions.do_character_detail("abc")


def test_sync_action_summarises_in_flight_and_failures(tenant, monkeypatch):
    monkeypatch.setattr(info_actions, "do_sync_for_tool", lambda tool_key: {
        "ok": False, "characters": {},
        "owners": [
            {"owner_id": 1, "skipped": "in_flight", "ok": True},
            {"owner_id": 2, "name": "Bob", "ok": False, "error": "boom"},
            {"owner_id": 3, "ok": True},
        ],
    })
    out = info_actions.do_sync_char_info()
    assert out["in_flight"] == [1]
    assert out["failed"] == [{"owner_id": 2, "name": "Bob", "error": "boom"}]


# -------------------------------------------------------------------- router
_TENANT = "00000000-0000-0000-0000-000000000def"
_client = TestClient(create_app())


def _enable_gate(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")


def _cookie():
    return {access_gate.SESSION_COOKIE_NAME:
            access_gate.create_session_token(1, "Some Character", _TENANT)}


def _provision(tools):
    storage.add_tenant_registry_entry(_TENANT, 1, character_name="Some Character")
    for tool in tools:
        storage.set_tool_grant(1, tool, _TENANT)


@pytest.fixture
def _gate_tables():
    tables = ("tool_grants", "tenant_registry_entries", "character_session_revocations",
              "access_requests", "access_allowlist")
    _client.cookies.clear()
    pg_helpers.wipe_tables(*tables)
    yield
    _client.cookies.clear()
    pg_helpers.wipe_tables(*tables)


def test_char_info_routes_need_the_char_info_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    _enable_gate(monkeypatch)
    monkeypatch.setattr(info_actions, "do_list_character_overview", lambda: {"characters": []})
    _provision(tools=("characters", "production"))   # everything but char_info
    denied = _client.get("/api/char-info/overview", cookies=_cookie())
    assert denied.status_code == 403
    storage.set_tool_grant(1, "char_info", _TENANT)
    allowed = _client.get("/api/char-info/overview", cookies=_cookie())
    assert allowed.status_code == 200
    assert allowed.json() == {"characters": []}


def test_char_info_router_converts_action_errors_to_400(monkeypatch):
    def boom(character_id):
        raise ActionError("nope")
    monkeypatch.setattr(info_actions, "do_character_detail", boom)
    resp = _client.get("/api/char-info/characters/1")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "nope"

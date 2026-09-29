"""Character Management phase 6: notifications (storage, fetcher, accessor
gate, YAML parsing, filters, local read flags, route grant)."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.character_management import notification_actions as na
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.access import read_esi
from eve_trader.esi_data.orchestrator import do_sync_for_tool
from eve_trader.esi_data.stale import clear_stale_owner_kind

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant, tenant_pair,
)
from .test_char_info import (  # noqa: F401
    ALICE, BOB, FakeClient, _TENANT, _cookie, _enable_gate, _gate_tables, _provision, _share, _token,
)

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

SCOPE = "esi-characters.read_notifications.v1"
_TABLES = ("character_notifications", "character_notification_reads", "esi_sharing", "esi_freshness", "tenant_tokens")
_client = TestClient(create_app())

ATTACK = (
    "charID: 1\nsolarsystemID: 30000142\nstructureShowInfoData: [showinfo, 35832, 1035466617946]\n"
    "shieldPercentage: 71.2\narmorPercentage: 100.0\nhullPercentage: 100.0\n"
)


def _raw(nid, type_, ts, text=None, is_read=False):
    return {"notification_id": nid, "type": type_, "sender_id": 1000125, "sender_type": "corporation",
            "timestamp": ts, "is_read": is_read, "text": text}


RAW = [
    _raw(1, "StructureUnderAttack", "2026-09-28T10:00:00Z", ATTACK),
    _raw(2, "WarDeclared", "2026-09-27T10:00:00Z", "againstID: 5\ndeclaredByID: 6\n"),
    _raw(3, "SomeBrandNewType", "2026-09-26T10:00:00Z", ": : not yaml [", is_read=True),
    _raw(4, "StructureLostShields", "2026-09-25T10:00:00Z", None),
]


class NotifClient(FakeClient):
    def __init__(self, raw=None):
        super().__init__()
        self.raw = RAW if raw is None else raw

    def character_notifications(self, cid, auth_role):
        self._rec("notifications", cid)
        return self.raw


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES)
    orchestrator._in_flight.clear()
    yield
    pg_helpers.wipe_tables(*_TABLES)


@pytest.fixture
def shared(tenant, monkeypatch):
    _token(ALICE, "Alice", scopes=SCOPE)
    _share(ALICE, "notifications", "char_notifications")
    monkeypatch.setattr(na.fields, "token_characters", lambda: [
        {"character_id": ALICE, "character_name": "Alice"}, {"character_id": BOB, "character_name": "Bob"}])
    do_sync_for_tool("char_notifications", client=NotifClient())


# ------------------------------------------------------------------- parsing
def test_parse_body_survives_garbage_and_oversize():
    assert na.parse_body(ATTACK)["solarsystemID"] == 30000142
    assert na.parse_body(": : not yaml [") == {}
    assert na.parse_body("- just\n- a list") == {}
    assert na.parse_body(None) == {} and na.parse_body("x" * (na.MAX_TEXT_CHARS + 1)) == {}
    assert na.parse_body("a: !!python/object/apply:os.system ['echo hi']") == {}      # safe loader only


def test_humanised_type_and_categories():
    assert na.humanise_type("StructureLostShields") == "Structure lost shields"
    assert na.humanise_type("") == "Notification"
    assert [na.category_of(t) for t in ("StructureUnderAttack", "SovStructureReinforced", "WarDeclared",
                                        "CorpAppNewMsg", "MoonminingExtractionFinished", "Mystery")] == [
        "structures", "sovereignty", "war", "corporation", "moon", "other"]


def test_summary_adds_place_structure_type_and_attack_levels():
    line = na.summarise("StructureUnderAttack", na.parse_body(ATTACK), {30000142: "Jita"}, {35832: "Astrahus"})
    assert line == "Structure under attack - Jita - Astrahus - shield 71%, armor 100%, hull 100%"
    assert na.summarise("Mystery", {}, {}, {}) == "Mystery"
    assert na.summarise("X", {"solarsystemID": 5}, {}, {}) == "X - system #5"


# ------------------------------------------------------------------- storage
def test_replace_mirrors_esi_and_prunes_local_flags(tenant):
    rows = [(1, "A", None, None, "2026-09-01T00:00:00Z", False, "t"), (2, "B", 5, "corporation", "2026-09-02T00:00:00Z", True, None)]
    storage.replace_character_notifications(ALICE, rows)
    assert storage.set_notifications_read(ALICE, [1, 2, 99], True) == 2               # 99 is not in the snapshot
    assert storage.load_notification_reads([ALICE]) == {(ALICE, 1), (ALICE, 2)}
    storage.replace_character_notifications(ALICE, rows[:1])
    assert storage.load_notification_reads([ALICE]) == {(ALICE, 1)}                   # flag for 2 dropped with the row
    assert [r[1] for r in storage.load_character_notifications([ALICE])] == [1]
    assert storage.set_notifications_read(ALICE, [1], False) == 1
    assert storage.load_notification_reads([ALICE]) == set()
    storage.replace_character_notifications(ALICE, [])
    assert storage.load_character_notifications([ALICE]) == []
    assert storage.load_character_notifications([]) == [] and storage.set_notifications_read(ALICE, [], True) == 0


def test_a_duplicate_id_from_esi_does_not_break_the_sync(tenant):
    storage.replace_character_notifications(ALICE, [(1, "A", None, None, "2026-09-01T00:00:00Z", False, None)] * 2)
    assert len(storage.load_character_notifications([ALICE])) == 1


def test_notification_rows_are_tenant_isolated(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        storage.replace_character_notifications(ALICE, [(1, "A", None, None, "2026-09-01T00:00:00Z", False, None)])
        storage.set_notifications_read(ALICE, [1], True)
    with storage.tenant_context(b):
        assert storage.load_character_notifications([ALICE]) == []
        assert storage.load_notification_reads([ALICE]) == set()


def test_stale_clear_removes_notifications_and_flags(tenant):
    storage.replace_character_notifications(ALICE, [(1, "A", None, None, "2026-09-01T00:00:00Z", False, None)])
    storage.set_notifications_read(ALICE, [1], True)
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=100)
    storage.upsert_esi_freshness("character", ALICE, "notifications", success=True)
    with storage.connect() as conn:
        conn.execute("UPDATE esi_freshness SET last_success_at = ? WHERE owner_id = ?", (old, ALICE))
    assert clear_stale_owner_kind("character", ALICE, "notifications", tier_interval_hours=6, stale_clear_multiples=3)
    assert storage.load_character_notifications([ALICE]) == [] and storage.load_notification_reads([ALICE]) == set()


def test_read_esi_is_fail_closed(tenant):
    storage.replace_character_notifications(ALICE, [(1, "A", None, None, "2026-09-01T00:00:00Z", False, None)])
    assert read_esi("notifications", "char_notifications") == []
    _share(ALICE, "notifications", "char_notifications")
    assert len(read_esi("notifications", "char_notifications")) == 1
    assert read_esi("notifications", "char_info") == []                               # another tool: nothing


# ------------------------------------------------------------------- actions
def test_list_orders_newest_first_with_names_and_flags(shared, monkeypatch):
    monkeypatch.setattr(storage, "get_solar_system_names", lambda ids: {30000142: "Jita"})
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda ids: {35832: (35832, 0, "Astrahus")})
    out = na.do_list_notifications()
    assert [i["notification_id"] for i in out["items"]] == [1, 2, 3, 4]
    first = out["items"][0]
    assert first["summary"].startswith("Structure under attack - Jita - Astrahus") and first["character_name"] == "Alice"
    assert first["read"] is False and out["items"][2]["read"] is True and out["items"][2]["read_in_game"] is True
    assert out["items"][2]["summary"] == "Some brand new type"
    assert out["total"] == 4 and out["unread_total"] == 3
    assert [h["character_id"] for h in out["hidden_characters"]] == [BOB]
    assert out["characters"][0]["synced_at"] is not None


def test_filters_and_paging(shared):
    assert [i["notification_id"] for i in na.do_list_notifications(type_filter="WarDeclared")["items"]] == [2]
    structures = na.do_list_notifications(category="structures")
    assert structures["total"] == 2 and {c["category"] for c in structures["categories"]} >= {"structures", "war", "other"}
    assert {t["type"] for t in structures["types"]} == {"StructureUnderAttack", "WarDeclared", "SomeBrandNewType",
                                                       "StructureLostShields"}      # pickers ignore their own filter
    page = na.do_list_notifications(limit=2, offset=1)
    assert [i["notification_id"] for i in page["items"]] == [2, 3] and page["total"] == 4
    assert na.do_list_notifications(limit="junk", offset=-5)["items"]                # bad paging falls back
    assert na.do_list_notifications(limit=10 ** 6)["items"]                          # clamped, not rejected


def test_marking_read_is_local_and_reflected(shared):
    assert na.do_set_notifications_read(ALICE, [1, 2], True) == {"changed": 2}
    out = na.do_list_notifications(unread_only=True)
    assert [i["notification_id"] for i in out["items"]] == [4] and out["unread_total"] == 1
    na.do_set_notifications_read(ALICE, [1], False)
    assert [i["notification_id"] for i in na.do_list_notifications(unread_only=True)["items"]] == [1, 4]
    assert na.do_set_notifications_read(ALICE, [3], False) == {"changed": 0}          # in-game-read stays read
    assert next(i for i in na.do_list_notifications()["items"] if i["notification_id"] == 3)["read"] is True


def test_detail_shows_the_parsed_body_or_says_it_could_not(shared):
    d = na.do_get_notification(ALICE, 1)
    assert {"key": "shieldPercentage", "value": "71.2"} in d["details"] and d["parsed"] is True
    assert {"key": "structureShowInfoData", "value": "showinfo, 35832, 1035466617946"} in d["details"]
    assert na.do_get_notification(ALICE, 3)["parsed"] is False
    with pytest.raises(ActionError):
        na.do_get_notification(ALICE, 12345)


def test_unshared_or_unknown_characters_are_refused(shared):
    for call in (lambda: na.do_list_notifications(character_id=BOB),
                 lambda: na.do_get_notification(BOB, 1),
                 lambda: na.do_set_notifications_read(BOB, [1]),
                 lambda: na.do_set_notifications_read(ALICE, []),
                 lambda: na.do_set_notifications_read(ALICE, ["x"])):
        with pytest.raises(ActionError):
            call()


def test_sync_reports_reauth_without_the_scope(tenant):
    _token(ALICE, "Alice", scopes="esi-mail.read_mail.v1")
    _share(ALICE, "notifications", "char_notifications")
    kinds = do_sync_for_tool("char_notifications", client=NotifClient())["owners"][0]["kinds"]
    assert kinds["notifications"].startswith("re-auth needed")


# --------------------------------------------------------------------- route
def test_the_routes_need_the_char_notifications_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    _enable_gate(monkeypatch)
    monkeypatch.setattr(na, "do_list_notifications", lambda **kw: {"items": []})
    _provision(tools=("characters", "char_mail"))
    assert _client.get("/api/char-notifications/notifications", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "char_notifications", _TENANT)
    ok = _client.get("/api/char-notifications/notifications", cookies=_cookie())
    assert ok.status_code == 200 and ok.json() == {"items": []}


def test_router_passes_filters_and_converts_action_errors(monkeypatch):
    seen = {}
    monkeypatch.setattr(na, "do_list_notifications", lambda **kw: seen.update(kw) or {"items": []})
    assert _client.get("/api/char-notifications/notifications",
                       params={"character_id": 7, "type": "T", "category": "war", "unread_only": "true",
                               "limit": 5, "offset": 10}).status_code == 200
    assert seen == {"character_id": 7, "type_filter": "T", "category": "war", "unread_only": True,
                    "limit": 5, "offset": 10}

    def boom(**kw):
        raise ActionError("nope")
    monkeypatch.setattr(na, "do_set_notifications_read", boom)
    r = _client.post("/api/char-notifications/read", json={"character_id": 1, "notification_ids": [1]})
    assert r.status_code == 400 and r.json()["detail"] == "nope"
    assert _client.post("/api/char-notifications/read", json={"character_id": 1, "notification_ids": []}).status_code == 422

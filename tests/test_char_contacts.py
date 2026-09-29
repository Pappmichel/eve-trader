"""Character Management phase 8: contacts and calendar (live, read-only, own
grant) and Character Info's live wallet journal."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.character_management import contacts_actions as ca
from eve_trader.character_management import info_actions
from eve_trader.esi_client import ESIClient, ESIError

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

SCOPES = "esi-characters.read_contacts.v1 esi-calendar.read_calendar_events.v1 esi-wallet.read_character_wallet.v1"
_TABLES = ("esi_sharing", "esi_freshness", "tenant_tokens")
_client = TestClient(create_app())


class ContactsClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.event_fetches = 0
        self.fail: Exception | None = None

    def _maybe_fail(self):
        if self.fail:
            raise self.fail

    def character_contacts(self, cid, role):
        self._rec("contacts", cid)
        self._maybe_fail()
        return [
            {"contact_id": 11, "contact_type": "character", "standing": 5.0, "label_ids": [1, 99], "is_watched": True},
            {"contact_id": 12, "contact_type": "corporation", "standing": -10.0, "is_blocked": True},
            {"contact_id": 13, "contact_type": "weird", "standing": 5.0},
        ]

    def character_contact_labels(self, cid, role):
        return [{"label_id": 1, "label_name": "Friends"}]

    def resolve_names_cached(self, ids):
        return {11: "Zed", 12: "Evil Corp"}

    def character_calendar(self, cid, role):
        self._rec("calendar", cid)
        self._maybe_fail()
        return [{"event_id": 2, "event_date": "2026-10-02T18:00:00Z", "title": "Fleet", "importance": 1},
                {"event_id": 1, "event_date": "2026-10-01T18:00:00Z", "title": None, "event_response": "accepted"}]

    def character_calendar_event(self, cid, role, event_id):
        self.event_fetches += 1
        return {"title": "Fleet", "date": "2026-10-02T18:00:00Z", "duration": 60, "text": "Bring guns",
                "owner_name": "FC", "owner_type": "character", "response": "accepted", "importance": 1}

    def character_wallet_journal_live(self, cid, role):
        self._rec("journal", cid)
        self._maybe_fail()
        return [
            {"id": 1, "date": "2026-09-01T00:00:00Z", "ref_type": "bounty_prizes", "amount": 1000.0, "balance": 1000.0},
            {"id": 3, "date": "2026-09-03T00:00:00Z", "ref_type": "market_escrow", "amount": -400.0, "balance": 1100.0,
             "description": "escrow"},
            {"id": 2, "date": "2026-09-02T00:00:00Z", "ref_type": "bounty_prizes", "amount": 500.0, "balance": 1500.0},
            {"date": None, "amount": 5},
        ]


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES)
    yield
    pg_helpers.wipe_tables(*_TABLES)


@pytest.fixture
def client(monkeypatch, tenant):
    c = ContactsClient()
    for mod in (ca, info_actions):
        monkeypatch.setattr(mod, "ESIClient", lambda tokens=None, _c=c: _c)
    for mod in (ca.fields, info_actions.fields):
        monkeypatch.setattr(mod, "token_characters", lambda: [
            {"character_id": ALICE, "character_name": "Alice"}, {"character_id": BOB, "character_name": "Bob"}])
    return c


# ------------------------------------------------------------------ contacts
def test_nothing_is_fetched_until_shared_and_scoped(client):
    _token(ALICE, "Alice", scopes="")
    assert ca.do_get_contacts(ALICE)["state"] == "not_shared"
    _share(ALICE, "contacts", "char_contacts")
    assert ca.do_get_contacts(ALICE)["state"] == "reauth_needed"
    assert ca.do_get_calendar(ALICE)["state"] == "not_shared"        # each kind is gated on its own
    assert client.calls == []


def test_contacts_are_named_labelled_and_ordered(client):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "contacts", "char_contacts")
    out = ca.do_get_contacts(ALICE)
    assert out["state"] == "ok"
    rows = out["value"]["contacts"]
    assert [r["name"] for r in rows] == ["#13", "Zed", "Evil Corp"]           # 5.0 (by name, "#" sorts first), then -10
    assert rows[1]["labels"] == ["Friends"] and rows[1]["is_watched"] is True   # unknown label id 99 is dropped
    assert rows[0]["contact_type"] == "other" and rows[2]["is_blocked"] is True
    assert out["value"]["labels"] == ["Friends"]


def test_an_esi_failure_is_a_redacted_field_state(client):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "contacts", "char_contacts")
    client.fail = ESIError("HTTP 403 for https://esi/secret?token=abc")
    out = ca.do_get_contacts(ALICE)
    assert out["state"] == "error" and out["detail"] == "ESI returned HTTP 403" and "secret" not in str(out)


def test_calendar_is_sorted_and_details_are_fetched_lazily(client):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "calendar", "char_contacts")
    events = ca.do_get_calendar(ALICE)["value"]
    assert [e["event_id"] for e in events] == [1, 2] and events[0]["title"] == "(untitled)"
    assert client.event_fetches == 0
    detail = ca.do_get_calendar_event(ALICE, 2)["value"]
    assert detail["text"] == "Bring guns" and client.event_fetches == 1


def test_an_event_id_not_on_the_calendar_is_refused(client):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "calendar", "char_contacts")
    with pytest.raises(ActionError, match="not on this character's calendar"):
        ca.do_get_calendar_event(ALICE, 424242)
    assert client.event_fetches == 0
    with pytest.raises(ActionError):
        ca.do_get_calendar_event(ALICE, "x")


def test_unregistered_characters_are_refused_and_the_list_needs_no_esi(client):
    with pytest.raises(ActionError, match="not registered"):
        ca.do_get_contacts(555)
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "contacts", "char_contacts")
    rows = {r["character_id"]: r for r in ca.do_list_characters()["characters"]}
    assert rows[ALICE]["contacts"]["state"] == "ok" and rows[ALICE]["calendar"]["state"] == "not_shared"
    assert rows[BOB]["contacts"]["state"] == "not_shared" and client.calls == []


def test_the_esi_client_caches_contacts_per_tenant_and_pages_them(tenant, monkeypatch):
    seen = []
    c = ESIClient.__new__(ESIClient)
    monkeypatch.setattr(c, "_get_all_pages", lambda path, params=None, auth_role=None: seen.append(path) or [])
    c.character_contacts(ALICE, "esi:1")
    c.character_contacts(ALICE, "esi:1")
    assert seen == [f"/characters/{ALICE}/contacts/"]


# -------------------------------------------------------------- wallet journal
def test_wallet_journal_is_gated_by_the_character_info_wallet_row(client):
    _token(ALICE, "Alice", scopes=SCOPES)
    assert info_actions.do_wallet_journal(ALICE)["state"] == "not_shared"
    _share(ALICE, "wallet", "char_info")                                  # the Trading kind does not open it
    assert info_actions.do_wallet_journal(ALICE)["state"] == "not_shared"
    _share(ALICE, "wallet_balance", "char_info")
    assert info_actions.do_wallet_journal(ALICE)["state"] == "ok"


def test_wallet_journal_summarises_the_whole_window(client, monkeypatch):
    _token(ALICE, "Alice", scopes=SCOPES)
    _share(ALICE, "wallet_balance", "char_info")
    out = info_actions.do_wallet_journal(ALICE)["value"]
    assert [e["id"] for e in out["entries"]] == [3, 2, 1]                  # newest first, the undated row is dropped
    assert (out["income"], out["expense"], out["total_entries"]) == (1500.0, -400.0, 3)
    assert out["by_type"][0] == {"ref_type": "bounty_prizes", "total": 1500.0}
    assert out["window_days"] == 30 and out["truncated"] is False
    monkeypatch.setattr(info_actions, "JOURNAL_MAX_ROWS", 2)
    capped = info_actions.do_wallet_journal(ALICE)["value"]
    assert len(capped["entries"]) == 2 and capped["truncated"] is True and capped["income"] == 1500.0


def test_wallet_journal_rejects_unknown_characters(client):
    with pytest.raises(ActionError):
        info_actions.do_wallet_journal(555)
    with pytest.raises(ActionError):
        info_actions.do_wallet_journal("x")


# --------------------------------------------------------------------- routes
def test_the_routes_need_the_char_contacts_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    _enable_gate(monkeypatch)
    monkeypatch.setattr(ca, "do_list_characters", lambda: {"characters": []})
    _provision(tools=("characters", "char_info"))
    assert _client.get("/api/char-contacts/characters", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "char_contacts", _TENANT)
    ok = _client.get("/api/char-contacts/characters", cookies=_cookie())
    assert ok.status_code == 200 and ok.json() == {"characters": []}


def test_routes_convert_action_errors_to_400(monkeypatch):
    def boom(**kw):
        raise ActionError("nope")
    monkeypatch.setattr(ca, "do_get_calendar_event", boom)
    monkeypatch.setattr(info_actions, "do_wallet_journal", boom)
    for url in ("/api/char-contacts/calendar/1/2", "/api/char-info/characters/1/wallet-journal"):
        r = _client.get(url)
        assert r.status_code == 400 and r.json()["detail"] == "nope"

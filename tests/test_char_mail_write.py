"""Character Management phase 4 (docs/CHARACTER_MANAGEMENT_PLAN.md): the mail
write actions - send, mark read, labels, delete, recipient lookup - and their
routes. Everything acts on a character's behalf in the game, so the gates are
tested as hard as the happy paths."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.auth import TokenRecord
from eve_trader.character_management import mail_actions, mail_archive, mail_write
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG
from eve_trader.esi_client import ESIClient, ESIDeliveryUnknown, ESIError, ESIHTTPError

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE, BOB = 1001, 1002
READ, SEND, ORGANIZE = "esi-mail.read_mail.v1", "esi-mail.send_mail.v1", "esi-mail.organize_mail.v1"
ALL_SCOPES = f"{READ} {SEND} {ORGANIZE}"

_MAIL_TABLES = (
    "char_mail_archive_settings", "mail_messages", "mail_recipients", "mail_character_headers",
    "mail_labels", "mail_lists",
)
_TABLES = (*_MAIL_TABLES, "esi_sharing", "esi_freshness", "tenant_tokens", "esi_character_capabilities")


@pytest.fixture(autouse=True)
def _wipe(monkeypatch):
    pg_helpers.wipe_tables(*_TABLES)
    monkeypatch.setattr(mail_archive, "_PAUSE_SECONDS", 0)
    yield
    pg_helpers.wipe_tables(*_TABLES)


def _share(cid):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, 'mail', 'char_mail') ON CONFLICT DO NOTHING", (cid,),
        )


def _token(cid, name, scopes=ALL_SCOPES):
    role = f"esi:{cid}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=cid, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=scopes,
    )))


def _setup(cid=ALICE, name="Alice", *, send=True, organize=True, scopes=ALL_SCOPES, archived=False):
    _token(cid, name, scopes)
    _share(cid)
    if send:
        storage.upsert_esi_character_capability(cid, "mail_send")
    if organize:
        storage.upsert_esi_character_capability(cid, "mail_organize")
    if archived:
        storage.enable_mail_archive(cid)


class FakeClient:
    def __init__(self):
        self.sent: list[dict] = []
        self.calls: list[tuple] = []
        self.send_result: object = 5000
        self.write_error: BaseException | None = None
        self.lists = {ALICE: [{"mailing_list_id": 7, "name": "Fleet List"}]}
        self.known = {
            "character": [{"id": 11, "name": "Bob Smith"}], "corporation": [{"id": 700, "name": "Some Corp"}],
            "alliance": [{"id": 900, "name": "Some Alliance"}],
        }
        self.resolved_names: list[list[str]] = []

    def character_mail_lists(self, cid, role):
        return self.lists.get(cid, [])

    def character_mail_labels(self, cid, role):
        return {"total_unread_count": 0, "labels": []}

    def resolve_recipient_names(self, names):
        self.resolved_names.append(list(names))
        wanted = {n.lower() for n in names}
        return {k: [x for x in v if x["name"].lower() in wanted] for k, v in self.known.items()}

    def search_entities(self, q, limit=8):
        self.calls.append(("search", q))
        return [{"type": "character", "id": 11, "name": "Bob Smith"}]

    def send_mail(self, cid, role, payload):
        self.calls.append(("send", cid, role))
        self.sent.append(payload)
        if isinstance(self.send_result, BaseException):
            raise self.send_result
        return self.send_result

    def _write(self, what, *args):
        self.calls.append((what, *args))
        if self.write_error:
            raise self.write_error

    def update_mail(self, cid, role, mail_id, changes):
        self._write("update", cid, mail_id, changes)

    def delete_mail(self, cid, role, mail_id):
        self._write("delete", cid, mail_id)

    def create_mail_label(self, cid, role, name, color):
        self._write("create_label", cid, name, color)
        return 77

    def delete_mail_label(self, cid, role, label_id):
        self._write("delete_label", cid, label_id)

    def resolve_names_cached(self, ids):
        return {i: f"Name{i}" for i in ids}


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mail_actions, "ESIClient", lambda tokens=None: client)
    return client


def _rec(rid=11, rtype="character", name=None):
    return {k: v for k, v in {"id": rid, "type": rtype, "name": name}.items() if v is not None}


def _count(table):
    with storage.connect() as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# --------------------------------------------------------------------- gates
@pytest.mark.parametrize("action,args", [
    (mail_write.do_send_mail, dict(recipients=[_rec()], subject="s", body="b")),
])
def test_send_needs_the_capability_ticked_not_just_the_scope(tenant, fake, action, args):
    _setup(send=False)                       # token has the send scope, capability not ticked
    with pytest.raises(ActionError, match='tick "Send mail"'):
        action(ALICE, **args)
    assert fake.sent == []


def test_send_needs_the_scope_even_when_the_capability_is_ticked(tenant, fake):
    _setup(scopes=READ)                     # ticked, but the token was never re-authorized
    with pytest.raises(ActionError, match="re-authorize"):
        mail_write.do_send_mail(ALICE, [_rec()], "s", "b")
    assert fake.sent == []


@pytest.mark.parametrize("call", [
    lambda: mail_write.do_mark_mail_read(ALICE, 5, True),
    lambda: mail_write.do_set_mail_labels(ALICE, 5, [32]),
    lambda: mail_write.do_delete_mail(ALICE, 5),
    lambda: mail_write.do_create_mail_label(ALICE, "x"),
    lambda: mail_write.do_delete_mail_label(ALICE, 32),
])
def test_organize_actions_need_their_own_capability(tenant, fake, call):
    _setup(organize=False)                  # send ticked, organize not: one does not imply the other
    with pytest.raises(ActionError, match='tick "Organize mail"'):
        call()
    assert fake.calls == []


def test_organize_needs_its_own_scope(tenant, fake):
    _setup(scopes=f"{READ} {SEND}")         # organize ticked but its scope is missing
    with pytest.raises(ActionError, match="re-authorize"):
        mail_write.do_mark_mail_read(ALICE, 5, True)
    assert fake.calls == []


def test_writes_refuse_unshared_and_unregistered_characters(tenant, fake):
    _token(ALICE, "Alice")
    storage.upsert_esi_character_capability(ALICE, "mail_send")        # ticked but not shared with Mail
    with pytest.raises(ActionError, match="not shared with Mail"):
        mail_write.do_send_mail(ALICE, [_rec()], "s", "b")
    with pytest.raises(ActionError, match="not registered"):
        mail_write.do_send_mail(424242, [_rec()], "s", "b")
    assert fake.sent == []


def test_folders_report_each_write_capability_state(tenant, fake):
    _setup(ALICE, "Alice")                                   # both ready
    _setup(BOB, "Bob", send=False, organize=True, scopes=READ)   # organize ticked, scope missing
    out = {c["character_id"]: c["capabilities"] for c in mail_actions.do_list_folders()["characters"]}
    assert out[ALICE] == {"send": "ready", "organize": "ready"}
    assert out[BOB] == {"send": "not_enabled", "organize": "reauth_needed"}


# ---------------------------------------------------------------------- send
def test_send_resolves_names_and_ids_and_dedupes(tenant, fake):
    _setup()
    result = mail_write.do_send_mail(
        ALICE,
        [
            {"name": "bob smith"},                                   # exact, case-insensitive
            {"id": 11, "type": "character", "name": "Bob Smith"},    # the same person again
            {"name": "Some Corp"},
            {"name": "Some Alliance", "type": "alliance"},
            {"name": "fleet list"},                                  # own mailing list by name
            {"id": 7, "type": "mailing_list"},                       # the same list by id
        ],
        "  Hello  ", "Body text",
    )
    assert result["sent"] is True and result["mail_id"] == 5000
    (payload,) = fake.sent
    assert payload["subject"] == "Hello" and payload["body"] == "Body text" and payload["approved_cost"] == 0
    assert payload["recipients"] == [
        {"recipient_id": 11, "recipient_type": "character"},
        {"recipient_id": 700, "recipient_type": "corporation"},
        {"recipient_id": 900, "recipient_type": "alliance"},
        {"recipient_id": 7, "recipient_type": "mailing_list"},
    ]
    assert [r["name"] for r in result["recipients"]] == ["Bob Smith", "Some Corp", "Some Alliance", "Fleet List"]
    assert fake.calls[0][:2] == ("send", ALICE)


def test_send_lists_every_name_it_could_not_find_and_sends_nothing(tenant, fake):
    _setup()
    with pytest.raises(ActionError) as exc:
        mail_write.do_send_mail(ALICE, [{"name": "Bob Smith"}, {"name": "Nobody"}, {"name": "Ghost Corp"}], "s", "b")
    assert "Nobody" in str(exc.value) and "Ghost Corp" in str(exc.value) and "Bob Smith" not in str(exc.value)
    assert fake.sent == []


def test_send_refuses_a_mailing_list_the_sender_is_not_subscribed_to(tenant, fake):
    _setup()
    with pytest.raises(ActionError, match="subscribed"):
        mail_write.do_send_mail(ALICE, [{"id": 999, "type": "mailing_list"}], "s", "b")
    assert fake.sent == []


@pytest.mark.parametrize("kwargs,match", [
    (dict(recipients=[], subject="s", body="b"), "at least one recipient"),
    (dict(recipients=[_rec()], subject="   ", body="b"), "needs a subject"),
    (dict(recipients=[_rec()], subject="x" * 1001, body="b"), "at most 1000"),
    (dict(recipients=[_rec()], subject="s", body="x" * 10001), "at most 10000"),
    (dict(recipients=[_rec(i) for i in range(1, 52)], subject="s", body="b"), "at most 50 recipients"),
    (dict(recipients=[{"type": "planet", "id": 1}], subject="s", body="b"), "Unknown recipient type"),
    (dict(recipients=[{"id": 5}], subject="s", body="b"), "needs a type"),
    (dict(recipients=[{}], subject="s", body="b"), "name or an id"),
    (dict(recipients=[_rec(-4)], subject="s", body="b"), "Invalid recipient id"),
    (dict(recipients=[_rec()], subject="s", body="b", approved_cost=-1), "Invalid approved_cost"),
])
def test_send_validates_up_front_and_never_calls_esi(tenant, fake, kwargs, match):
    _setup()
    with pytest.raises(ActionError, match=match):
        mail_write.do_send_mail(ALICE, **kwargs)
    assert fake.sent == []


def test_an_unknown_delivery_tells_the_user_to_check_sent_before_resending(tenant, fake):
    _setup(archived=True)
    fake.send_result = ESIDeliveryUnknown("Request failed for https://esi/x: timeout")
    with pytest.raises(ActionError, match="Check the Sent folder before sending it again"):
        mail_write.do_send_mail(ALICE, [_rec()], "Secret subject", "Secret body")
    assert len(fake.sent) == 1                               # exactly one attempt
    assert all(_count(t) == 0 for t in ("mail_messages", "mail_character_headers"))   # nothing archived as sent


def test_cspa_charge_asks_for_approval_then_sends_with_the_approved_cost(tenant, fake):
    _setup()
    fake.send_result = ESIHTTPError(400, '{"error": "The CSPA charge cost 12.50 ISK - approve it"}', "HTTP 400 for u")
    first = mail_write.do_send_mail(ALICE, [_rec()], "s", "b")
    assert first == {"sent": False, "needs_approval": True, "cost": 12.5}
    fake.send_result = 5001
    second = mail_write.do_send_mail(ALICE, [_rec()], "s", "b", approved_cost=13)
    assert second["sent"] is True
    assert fake.sent[-1]["approved_cost"] == 13


def test_a_refusal_after_approval_is_an_error_not_another_approval_prompt(tenant, fake):
    _setup()
    fake.send_result = ESIHTTPError(400, "cost 12.50 ISK", "HTTP 400 for u")
    with pytest.raises(ActionError, match="ESI refused to send the mail \\(ESI returned HTTP 400\\)"):
        mail_write.do_send_mail(ALICE, [_rec()], "s", "b", approved_cost=20)


def test_refusals_never_leak_the_response_text(tenant, fake):
    _setup()
    fake.send_result = ESIHTTPError(403, '{"error": "recipient Secret Person blocked you"}', "HTTP 403 for u")
    with pytest.raises(ActionError) as exc:
        mail_write.do_send_mail(ALICE, [_rec()], "s", "b")
    assert "Secret" not in str(exc.value) and "HTTP 403" in str(exc.value)


def test_sending_stores_the_mail_in_an_archived_senders_archive_but_not_a_live_one(tenant, fake):
    _setup(ALICE, "Alice", archived=True)
    _setup(BOB, "Bob")
    storage.upsert_esi_character_capability(BOB, "mail_send")
    mail_write.do_send_mail(ALICE, [_rec(11)], "Hi", "Archived body")
    stored = storage.get_archived_mail(ALICE, 5000)
    assert stored["subject"] == "Hi" and stored["body"] == "Archived body"
    assert stored["labels"] == [2] and stored["is_read"] is True and stored["from_id"] == ALICE
    assert stored["recipients"] == [{"recipient_id": 11, "recipient_type": "character"}]
    before = _count("mail_messages")
    fake.send_result = 5002
    mail_write.do_send_mail(BOB, [_rec(11)], "Live", "Live body")
    assert _count("mail_messages") == before                 # a live-mode character stores nothing


def test_writes_invalidate_the_live_mail_cache_of_that_character_only(tenant, fake):
    _setup()
    tenant_id = str(storage.get_current_tenant())
    ESIClient._live_character_cache[(tenant_id, "mail_headers", ALICE, (), 0)] = ["stale"]
    ESIClient._live_character_cache_at[(tenant_id, "mail_headers", ALICE, (), 0)] = 9e12
    ESIClient._live_character_cache[(tenant_id, "mail_headers", BOB, (), 0)] = ["other"]
    ESIClient._live_character_cache_at[(tenant_id, "mail_headers", BOB, (), 0)] = 9e12
    ESIClient._live_character_cache[(tenant_id, "location", ALICE)] = {"x": 1}
    ESIClient._live_character_cache_at[(tenant_id, "location", ALICE)] = 9e12
    mail_write.do_send_mail(ALICE, [_rec()], "s", "b")
    assert (tenant_id, "mail_headers", ALICE, (), 0) not in ESIClient._live_character_cache
    assert (tenant_id, "mail_headers", BOB, (), 0) in ESIClient._live_character_cache
    assert (tenant_id, "location", ALICE) in ESIClient._live_character_cache


def test_recipient_search_combines_own_lists_and_public_search(tenant, fake):
    _setup()
    out = mail_write.do_search_recipients(ALICE, "fle")
    assert {"type": "mailing_list", "id": 7, "name": "Fleet List"} in out["results"]
    assert any(r["type"] == "character" for r in out["results"])
    with pytest.raises(ActionError, match="at least 3"):
        mail_write.do_search_recipients(ALICE, "ab")


def test_recipient_search_failure_is_redacted(tenant, fake, monkeypatch):
    _setup()
    monkeypatch.setattr(fake, "search_entities", lambda q, limit=8: (_ for _ in ()).throw(ESIError("HTTP 500 for u: secret")))
    with pytest.raises(ActionError) as exc:
        mail_write.do_search_recipients(ALICE, "abc")
    assert "secret" not in str(exc.value)


# ------------------------------------------------------------------ organize
def test_mark_read_updates_esi_and_mirrors_into_the_archive(tenant, fake):
    _setup(archived=True)
    storage.store_mail_headers(ALICE, [{"mail_id": 5, "from": 9, "subject": "s", "timestamp": "2026-09-01T00:00:00Z",
                                        "is_read": False, "labels": [1], "recipients": []}])
    out = mail_write.do_mark_mail_read(ALICE, 5, True)
    assert out == {"character_id": ALICE, "mail_id": 5, "read": True}
    assert ("update", ALICE, 5, {"read": True}) in fake.calls
    assert storage.get_archived_mail(ALICE, 5)["is_read"] is True
    mail_write.do_mark_mail_read(ALICE, 5, False)
    assert storage.get_archived_mail(ALICE, 5)["is_read"] is False


def test_set_labels_normalises_and_mirrors(tenant, fake):
    _setup(archived=True)
    storage.store_mail_headers(ALICE, [{"mail_id": 5, "from": 9, "subject": "s", "timestamp": "2026-09-01T00:00:00Z",
                                        "is_read": False, "labels": [1], "recipients": []}])
    out = mail_write.do_set_mail_labels(ALICE, 5, [40, 1, 40, 32])
    assert out["labels"] == [1, 32, 40]
    assert ("update", ALICE, 5, {"labels": [1, 32, 40]}) in fake.calls
    assert storage.get_archived_mail(ALICE, 5)["labels"] == [1, 32, 40]
    with pytest.raises(ActionError, match="list of label ids"):
        mail_write.do_set_mail_labels(ALICE, 5, "32")                # type: ignore[arg-type]
    with pytest.raises(ActionError, match="Invalid label id"):
        mail_write.do_set_mail_labels(ALICE, 5, [0])


def test_delete_removes_the_mail_in_the_game_and_from_the_archive(tenant, fake):
    _setup(ALICE, "Alice", archived=True)
    _setup(BOB, "Bob", archived=True)
    hdr = {"mail_id": 5, "from": 9, "subject": "s", "timestamp": "2026-09-01T00:00:00Z", "is_read": True,
           "labels": [1], "recipients": [{"recipient_id": 700, "recipient_type": "corporation"}]}
    storage.store_mail_headers(ALICE, [hdr])
    storage.store_mail_headers(BOB, [hdr])
    mail_write.do_delete_mail(ALICE, 5)
    assert ("delete", ALICE, 5) in fake.calls
    assert storage.get_archived_mail(ALICE, 5) is None
    assert storage.get_archived_mail(BOB, 5) is not None            # Bob's copy of the same message stays
    assert _count("mail_messages") == 1
    mail_write.do_delete_mail(BOB, 5)
    assert _count("mail_messages") == 0 and _count("mail_recipients") == 0     # last reference gone -> collected


def test_organize_failures_are_redacted_and_do_not_touch_the_archive(tenant, fake):
    _setup(archived=True)
    storage.store_mail_headers(ALICE, [{"mail_id": 5, "from": 9, "subject": "s", "timestamp": "2026-09-01T00:00:00Z",
                                        "is_read": False, "labels": [1], "recipients": []}])
    fake.write_error = ESIHTTPError(404, '{"error": "secret subject not found"}', "HTTP 404 for u")
    with pytest.raises(ActionError) as exc:
        mail_write.do_delete_mail(ALICE, 5)
    assert "secret" not in str(exc.value) and "HTTP 404" in str(exc.value)
    assert storage.get_archived_mail(ALICE, 5) is not None
    with pytest.raises(ActionError):
        mail_write.do_mark_mail_read(ALICE, 5, True)
    assert storage.get_archived_mail(ALICE, 5)["is_read"] is False


def test_create_label_validates_and_resyncs_an_archived_characters_labels(tenant, fake, monkeypatch):
    _setup(archived=True)
    synced = []
    monkeypatch.setattr(mail_archive, "sync_labels_and_lists", lambda cid, client, role: synced.append(cid))
    out = mail_write.do_create_mail_label(ALICE, "  Contracts ", "#FF6600")
    assert out == {"character_id": ALICE, "label_id": 77, "name": "Contracts", "color": "#ff6600"}
    assert ("create_label", ALICE, "Contracts", "#ff6600") in fake.calls and synced == [ALICE]
    assert mail_write.do_create_mail_label(ALICE, "Plain")["color"] == "#ffffff"
    for name, color, match in [("", None, "needs a name"), ("x" * 41, None, "at most 40"),
                               ("ok", "#123456", "fixed label colours")]:
        with pytest.raises(ActionError, match=match):
            mail_write.do_create_mail_label(ALICE, name, color)


def test_delete_label_refuses_the_builtin_folders(tenant, fake):
    _setup()
    for label_id in (1, 2, 4, 8, 16):
        with pytest.raises(ActionError, match="built-in folders"):
            mail_write.do_delete_mail_label(ALICE, label_id)
    assert mail_write.do_delete_mail_label(ALICE, 32) == {"character_id": ALICE, "label_id": 32, "deleted": True}
    assert ("delete_label", ALICE, 32) in fake.calls


def test_a_live_character_never_writes_archive_rows_when_organizing(tenant, fake):
    _setup()
    mail_write.do_mark_mail_read(ALICE, 5, True)
    mail_write.do_set_mail_labels(ALICE, 5, [1])
    mail_write.do_delete_mail(ALICE, 5)
    assert all(_count(t) == 0 for t in _MAIL_TABLES)


# -------------------------------------------------------------------- router
_TENANT = "00000000-0000-0000-0000-000000000cad"
_client = TestClient(create_app())


def _cookie():
    return {access_gate.SESSION_COOKIE_NAME:
            access_gate.create_session_token(1, "Some Character", _TENANT)}


@pytest.fixture
def _gate_tables():
    tables = ("tool_grants", "tenant_registry_entries", "character_session_revocations",
              "access_requests", "access_allowlist")
    _client.cookies.clear()
    pg_helpers.wipe_tables(*tables)
    yield
    _client.cookies.clear()
    pg_helpers.wipe_tables(*tables)


def test_write_routes_need_the_char_mail_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    monkeypatch.setattr(mail_write, "do_send_mail", lambda **kw: {"sent": True})
    body = {"from_character_id": 1, "recipients": [{"name": "x"}], "subject": "s", "body": "b"}
    storage.add_tenant_registry_entry(_TENANT, 1, character_name="Some Character")
    storage.set_tool_grant(1, "char_skills", _TENANT)
    assert _client.post("/api/char-mail/send", json=body, cookies=_cookie()).status_code == 403
    assert _client.delete("/api/char-mail/mails/1/2", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "char_mail", _TENANT)
    assert _client.post("/api/char-mail/send", json=body, cookies=_cookie()).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/char-mail/send", {"from_character_id": 1, "recipients": [{"name": "x"}], "subject": "s"}),
    ("post", "/api/char-mail/mails/1/2/read", {"read": True}),
    ("post", "/api/char-mail/mails/1/2/labels", {"labels": [32]}),
    ("delete", "/api/char-mail/mails/1/2", None),
    ("post", "/api/char-mail/labels", {"character_id": 1, "name": "x"}),
    ("delete", "/api/char-mail/labels/1/32", None),
    ("post", "/api/char-mail/archive", {"character_id": 1, "enabled": True}),
])
def test_write_routes_reject_a_foreign_origin_before_doing_anything(monkeypatch, method, path, body):
    """CSRF: a page on another origin must not be able to send/delete mail with
    the victim's cookies (the global Origin check in api/app.py)."""
    monkeypatch.setattr(mail_write, "do_send_mail", lambda **kw: pytest.fail("action must not run"))
    monkeypatch.setattr(mail_write, "do_delete_mail", lambda **kw: pytest.fail("action must not run"))
    monkeypatch.setattr(mail_actions, "do_set_mail_archive", lambda **kw: pytest.fail("action must not run"))
    resp = getattr(_client, method)(path, headers={"Origin": "https://evil.example"}, **({"json": body} if body else {}))
    assert resp.status_code == 403 and "origin" in resp.json()["detail"].lower()


def test_routes_pass_their_arguments_to_the_actions(monkeypatch):
    seen: dict = {}
    for name in ("do_send_mail", "do_mark_mail_read", "do_set_mail_labels", "do_delete_mail",
                 "do_create_mail_label", "do_delete_mail_label", "do_search_recipients"):
        monkeypatch.setattr(mail_write, name, (lambda n: lambda **kw: seen.update({n: kw}) or {"ok": True})(name))
    _client.post("/api/char-mail/send", json={
        "from_character_id": 5, "recipients": [{"type": "character", "id": 11}, {"name": "Some Corp"}],
        "subject": "S", "body": "B", "approved_cost": 9,
    })
    assert seen["do_send_mail"] == {
        "from_character_id": 5, "recipients": [{"type": "character", "id": 11}, {"name": "Some Corp"}],
        "subject": "S", "body": "B", "approved_cost": 9,
    }
    _client.post("/api/char-mail/mails/5/6/read", json={"read": False})
    assert seen["do_mark_mail_read"] == {"character_id": 5, "mail_id": 6, "read": False}
    _client.post("/api/char-mail/mails/5/6/labels", json={"labels": [1, 32]})
    assert seen["do_set_mail_labels"] == {"character_id": 5, "mail_id": 6, "labels": [1, 32]}
    _client.delete("/api/char-mail/mails/5/6")
    assert seen["do_delete_mail"] == {"character_id": 5, "mail_id": 6}
    _client.post("/api/char-mail/labels", json={"character_id": 5, "name": "N", "color": "#ffffff"})
    assert seen["do_create_mail_label"] == {"character_id": 5, "name": "N", "color": "#ffffff"}
    _client.delete("/api/char-mail/labels/5/32")
    assert seen["do_delete_mail_label"] == {"character_id": 5, "label_id": 32}
    _client.get("/api/char-mail/recipients", params={"character_id": 5, "q": "abc"})
    assert seen["do_search_recipients"] == {"character_id": 5, "q": "abc"}


def test_write_route_action_errors_become_400s(monkeypatch):
    def boom(**kw):
        raise ActionError("not allowed")
    monkeypatch.setattr(mail_write, "do_send_mail", boom)
    resp = _client.post("/api/char-mail/send", json={"from_character_id": 1, "recipients": [], "subject": "s"})
    assert resp.status_code == 400 and resp.json()["detail"] == "not allowed"

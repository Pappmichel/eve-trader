"""Character Management phase 3 (docs/CHARACTER_MANAGEMENT_PLAN.md): Mail.
Live by default (nothing stored), opt-in per-character archive with a
resumable backfill, gated by sharing and scope, never leaking mail into
errors."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.auth import TokenManager, TokenRecord
from eve_trader.character_management import mail_actions, mail_archive
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG
from eve_trader.esi_client import ESIError

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
BOB = 1002
CAROL = 1003
MAIL_SCOPE = "esi-mail.read_mail.v1"

_MAIL_TABLES = (
    "char_mail_archive_settings", "mail_messages", "mail_recipients", "mail_character_headers",
    "mail_labels", "mail_lists",
)
_TABLES = (*_MAIL_TABLES, "esi_sharing", "esi_freshness", "tenant_tokens")


@pytest.fixture(autouse=True)
def _wipe(monkeypatch):
    pg_helpers.wipe_tables(*_TABLES)
    monkeypatch.setattr(mail_archive, "_PAUSE_SECONDS", 0)
    mail_archive._threads.clear()
    yield
    pg_helpers.wipe_tables(*_TABLES)


def _share(owner_id, tool="char_mail", kind="mail"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
            (owner_id, kind, tool),
        )


def _token(cid, name, scopes=MAIL_SCOPE):
    role = f"esi:{cid}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=cid, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=scopes,
    )))


def _raw(mail_id, *, sender=9001, subject=None, ts=None, read=False, labels=(1,), recipients=None):
    return {
        "mail_id": mail_id, "from": sender, "subject": subject or f"Subject {mail_id}",
        # monotonic by default (newer mail_id == newer timestamp), like a real mailbox
        "timestamp": ts or (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=mail_id)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "is_read": read, "labels": list(labels),
        "recipients": recipients or [{"recipient_id": ALICE, "recipient_type": "character"}],
    }


class FakeMailClient:
    """A mailbox per character in ESI's raw shape, paged like ESI (50 per
    page, newest mail_id first, `last_mail_id` cursor, optional label filter)."""

    def __init__(self):
        self.mailboxes: dict[int, list[dict]] = {}
        self.bodies: dict[int, str] = {}
        self.body_errors: dict[int, str] = {}
        self.header_error_after_pages: dict[int, int] = {}
        self.labels: dict[int, dict] = {}
        self.lists: dict[int, list[dict]] = {}
        self.calls: list[tuple] = []
        self.on_header_page = None          # hook(cid, page_no)
        self._pages: dict[int, int] = {}

    def character_mail_headers(self, cid, role, labels=None, last_mail_id=None, cache=True):
        page_no = self._pages[cid] = self._pages.get(cid, 0) + 1
        self.calls.append(("headers", cid, tuple(labels or ()), last_mail_id, cache))
        if self.on_header_page:
            self.on_header_page(cid, page_no)
        limit = self.header_error_after_pages.get(cid)
        if limit is not None and page_no > limit:
            raise ESIError("HTTP 500 for https://esi/mail: {\"error\": \"body-of-a-secret-mail\"}")
        rows = sorted(self.mailboxes.get(cid, []), key=lambda r: -r["mail_id"])
        if labels:
            rows = [r for r in rows if set(labels) & set(r["labels"])]
        if last_mail_id:
            rows = [r for r in rows if r["mail_id"] < last_mail_id]
        return rows[:50]

    def character_mail_body(self, cid, role, mail_id, cache=True):
        self.calls.append(("body", cid, mail_id, cache))
        if mail_id in self.body_errors:
            raise ESIError(self.body_errors[mail_id])
        row = next(r for r in self.mailboxes[cid] if r["mail_id"] == mail_id)
        return {
            "body": self.bodies.get(mail_id, f"Body {mail_id}"), "from": row["from"],
            "subject": row["subject"], "timestamp": row["timestamp"], "labels": row["labels"],
            "read": row["is_read"], "recipients": row["recipients"],
        }

    def character_mail_labels(self, cid, role):
        self.calls.append(("labels", cid))
        return self.labels.get(cid, {"total_unread_count": 0, "labels": []})

    def character_mail_lists(self, cid, role):
        self.calls.append(("lists", cid))
        return self.lists.get(cid, [])

    def resolve_names_cached(self, ids):
        return {i: f"Name{i}" for i in ids}


@pytest.fixture
def fake(monkeypatch):
    client = FakeMailClient()
    monkeypatch.setattr(mail_actions, "ESIClient", lambda tokens=None: client)
    return client


def _count(table):
    with storage.connect() as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _setup_alice(archived=False):
    _token(ALICE, "Alice")
    _share(ALICE)
    if archived:
        storage.enable_mail_archive(ALICE)


# ------------------------------------------------------ archive storage
def test_archive_page_orders_by_mail_id_filters_labels_and_pages_by_cursor(tenant):
    storage.enable_mail_archive(ALICE)
    storage.store_mail_headers(ALICE, [
        _raw(10, labels=(1,)), _raw(30, labels=(2,)), _raw(20, labels=(1, 8)), _raw(5, labels=(1,)),
    ])
    assert [h["mail_id"] for h in storage.load_mail_archive_page(ALICE, None, None, 50)] == [30, 20, 10, 5]
    assert [h["mail_id"] for h in storage.load_mail_archive_page(ALICE, 1, None, 50)] == [20, 10, 5]
    assert [h["mail_id"] for h in storage.load_mail_archive_page(ALICE, 8, None, 50)] == [20]
    assert [h["mail_id"] for h in storage.load_mail_archive_page(ALICE, None, 20, 50)] == [10, 5]
    assert [h["mail_id"] for h in storage.load_mail_archive_page(ALICE, None, None, 2)] == [30, 20]
    row = storage.load_mail_archive_page(ALICE, 8, None, 50)[0]
    assert row["recipients"] == [{"recipient_id": ALICE, "recipient_type": "character"}]
    assert row["is_read"] is False and row["labels"] == [1, 8]


def test_store_headers_updates_read_state_but_never_blanks_a_stored_body(tenant):
    storage.enable_mail_archive(ALICE)
    storage.store_mail_headers(ALICE, [_raw(10, read=False)])
    assert storage.store_mail_body(ALICE, 10, "the body") is True
    storage.store_mail_headers(ALICE, [_raw(10, read=True, labels=(1, 32))])
    row = storage.get_archived_mail(ALICE, 10)
    assert row["body"] == "the body"
    assert row["is_read"] is True and row["labels"] == [1, 32]


def test_writes_are_refused_for_a_character_without_an_enabled_archive(tenant):
    """The guard that stops a background thread re-creating rows after the
    user deleted the archive."""
    assert storage.store_mail_headers(ALICE, [_raw(10)]) is False
    assert storage.replace_mail_labels(ALICE, [(1, "Inbox", None, 0)]) is False
    assert storage.replace_mail_lists(ALICE, [(7, "List")]) is False
    assert storage.store_mail_body(ALICE, 10, "x") is False
    assert all(_count(t) == 0 for t in _MAIL_TABLES)


def test_a_message_two_characters_received_is_stored_once_and_gc_keeps_it(tenant):
    storage.enable_mail_archive(ALICE)
    storage.enable_mail_archive(BOB)
    storage.store_mail_headers(ALICE, [_raw(10), _raw(11)])
    storage.store_mail_headers(BOB, [_raw(10, read=True)])
    assert _count("mail_messages") == 2                 # mail 10 stored once
    deleted = storage.delete_mail_archive(ALICE)
    assert deleted == {"headers": 2, "messages": 1}      # only mail 11 was orphaned
    assert storage.get_archived_mail(BOB, 10) is not None
    assert storage.get_archived_mail(ALICE, 10) is None
    assert _count("mail_recipients") == 1               # mail 11's recipient row was collected
    assert ALICE not in storage.get_mail_archive_settings()


def test_delete_leaves_other_characters_and_a_later_write_is_refused(tenant):
    storage.enable_mail_archive(ALICE)
    storage.store_mail_headers(ALICE, [_raw(10)])
    storage.delete_mail_archive(ALICE)
    assert storage.store_mail_headers(ALICE, [_raw(10)]) is False     # the in-flight backfill's next page
    assert _count("mail_messages") == 0 and _count("mail_character_headers") == 0


def test_archive_rows_are_tenant_isolated(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        storage.enable_mail_archive(ALICE)
        storage.store_mail_headers(ALICE, [_raw(10)])
    with storage.tenant_context(b):
        assert storage.load_mail_archive_page(ALICE, None, None, 50) == []
        assert storage.get_archived_mail(ALICE, 10) is None
        assert storage.get_mail_archive_settings() == {}
        # the same mail_id may exist independently in another tenant
        storage.enable_mail_archive(ALICE)
        storage.store_mail_headers(ALICE, [_raw(10, subject="other tenant")])
    with storage.tenant_context(a):
        assert storage.get_archived_mail(ALICE, 10)["subject"] == "Subject 10"


def test_search_finds_subject_and_body_and_escapes_wildcards(tenant):
    storage.enable_mail_archive(ALICE)
    storage.store_mail_headers(ALICE, [
        _raw(10, subject="Fleet doctrine update"), _raw(11, subject="100% done"), _raw(12, subject="unrelated"),
    ])
    storage.store_mail_body(ALICE, 12, "mentions the doctrine deep inside the body")
    found = {h["mail_id"] for h in storage.search_mail_archive([ALICE], "doctrine", 50)}
    assert found == {10, 12}                                     # subject hit + body hit
    assert {h["mail_id"] for h in storage.search_mail_archive([ALICE], "doctr", 50)} == {10}   # partial subject
    assert {h["mail_id"] for h in storage.search_mail_archive([ALICE], "100%", 50)} == {11}
    # "%" is a literal here, not a match-everything wildcard: only the subject that contains one
    assert {h["mail_id"] for h in storage.search_mail_archive([ALICE], "%", 50)} == {11}
    assert storage.search_mail_archive([], "doctrine", 50) == []
    assert storage.search_mail_archive([BOB], "doctrine", 50) == []          # other character


# --------------------------------------------------------------- live mode
def test_live_mode_reads_ten_thousand_lines_and_stores_nothing(tenant, fake):
    _setup_alice()
    fake.mailboxes[ALICE] = [_raw(i) for i in range(1, 8)]
    fake.labels[ALICE] = {"total_unread_count": 3, "labels": []}
    mail_actions.do_list_folders()
    listed = mail_actions.do_list_mails()
    opened = mail_actions.do_open_mail(ALICE, 3)
    assert len(listed["mails"]) == 7 and opened["body"] == "Body 3"
    assert all(_count(t) == 0 for t in _MAIL_TABLES)      # nothing about the mail was written


def test_list_mails_groups_a_mail_received_by_two_characters(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE)
    _share(BOB)
    corp_mail = _raw(50, read=True, ts="2026-09-30T00:00:00Z",
                     recipients=[{"recipient_id": 700, "recipient_type": "corporation"}])
    fake.mailboxes[ALICE] = [corp_mail, _raw(40, ts="2026-08-01T00:00:00Z")]
    fake.mailboxes[BOB] = [{**corp_mail, "is_read": False}, _raw(60, ts="2026-09-01T00:00:00Z")]
    result = mail_actions.do_list_mails()
    # the timestamp decides the order, not the mail_id (50 is newest despite the lower id)
    assert [m["mail_id"] for m in result["mails"]] == [50, 60, 40]
    corp = next(m for m in result["mails"] if m["mail_id"] == 50)
    assert {r["character_name"] for r in corp["received_by"]} == {"Alice", "Bob"}
    assert corp["is_read"] is False                       # unread for Bob -> unread overall
    assert corp["from_name"] == "Name9001" and corp["recipients"][0]["name"] == "Name700"


def test_unshared_characters_are_never_fetched(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE)                      # Bob is not shared
    fake.mailboxes[ALICE] = [_raw(1)]
    fake.mailboxes[BOB] = [_raw(2)]
    result = mail_actions.do_list_mails()
    assert {m["mail_id"] for m in result["mails"]} == {1}
    assert BOB not in {c[1] for c in fake.calls}
    with pytest.raises(ActionError, match="not shared with Mail"):
        mail_actions.do_list_mails(character_id=BOB)
    with pytest.raises(ActionError, match="not registered"):
        mail_actions.do_list_mails(character_id=424242)


def test_paging_uses_a_cursor_per_character_and_only_refetches_characters_with_more(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE)
    _share(BOB)
    fake.mailboxes[ALICE] = [_raw(i) for i in range(1, 121)]       # 3 pages
    fake.mailboxes[BOB] = [_raw(1000 + i) for i in range(1, 11)]   # one short page
    first = mail_actions.do_list_mails()
    assert len(first["mails"]) == 60
    assert first["next_cursors"] == {str(ALICE): 71}               # Bob is exhausted, Alice has more
    fake.calls.clear()
    second = mail_actions.do_list_mails(cursors=first["next_cursors"])
    assert {c[1] for c in fake.calls if c[0] == "headers"} == {ALICE}
    assert fake.calls[0][3] == 71
    assert [m["mail_id"] for m in second["mails"]][0] == 70
    assert second["next_cursors"] == {str(ALICE): 21}


def test_label_filter_is_passed_to_esi(tenant, fake):
    _setup_alice()
    fake.mailboxes[ALICE] = [_raw(1, labels=(1,)), _raw(2, labels=(2,))]
    inbox = mail_actions.do_list_mails(label_id=1)
    assert [m["mail_id"] for m in inbox["mails"]] == [1]
    assert ("headers", ALICE, (1,), None, True) in fake.calls


def test_character_without_the_mail_scope_reports_reauth_but_others_load(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob", scopes="esi-skills.read_skills.v1")
    _share(ALICE)
    _share(BOB)
    fake.mailboxes[ALICE] = [_raw(1)]
    result = mail_actions.do_list_mails()
    states = {c["character_id"]: c["state"] for c in result["characters"]}
    assert states == {ALICE: "ok", BOB: "reauth_needed"}
    assert len(result["mails"]) == 1
    assert BOB not in {c[1] for c in fake.calls}
    with pytest.raises(ActionError, match="re-authorize"):
        mail_actions.do_open_mail(BOB, 1)


def test_esi_errors_never_leak_response_text(tenant, fake):
    _setup_alice()
    fake.mailboxes[ALICE] = [_raw(1)]
    fake.header_error_after_pages[ALICE] = 0
    result = mail_actions.do_list_mails()
    (status,) = result["characters"]
    assert status["state"] == "error" and status["detail"] == "ESI returned HTTP 500"
    assert "secret" not in json.dumps(result)


def test_open_mail_error_is_redacted(tenant, fake):
    _setup_alice()
    fake.mailboxes[ALICE] = [_raw(1)]
    fake.body_errors[1] = "HTTP 404 for https://esi/mail/1: {\"error\": \"secret-subject\"}"
    with pytest.raises(ActionError) as exc:
        mail_actions.do_open_mail(ALICE, 1)
    assert "secret" not in str(exc.value) and "HTTP 404" in str(exc.value)


def test_folders_always_list_system_folders_and_sum_unread(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE)
    _share(BOB)
    fake.labels[ALICE] = {"total_unread_count": 5, "labels": [
        {"label_id": 1, "name": "[Inbox]", "unread_count": 3, "color": "#fff"},
        {"label_id": 32, "name": "Contracts", "unread_count": 2},
    ]}
    fake.labels[BOB] = {"total_unread_count": 1, "labels": [{"label_id": 1, "name": "[Inbox]", "unread_count": 1}]}
    fake.lists[ALICE] = [{"mailing_list_id": 7, "name": "Fleet list"}]
    out = mail_actions.do_list_folders()
    alice = next(c for c in out["characters"] if c["character_id"] == ALICE)
    names = [lb["name"] for lb in alice["labels"]]
    assert names[:5] == ["Inbox", "Sent", "Corp", "Alliance", "Mailing lists"]      # clean names, fixed order
    assert names[5:] == ["Contracts"] and alice["labels"][5]["system"] is False
    assert alice["lists"] == [{"list_id": 7, "name": "Fleet list"}]
    assert out["unread"]["1"] == 4                       # Inbox unread summed across characters
    bob = next(c for c in out["characters"] if c["character_id"] == BOB)
    assert [lb["name"] for lb in bob["labels"]][:2] == ["Inbox", "Sent"]      # ESI omitted the rest


def test_mailing_list_recipients_are_named_from_the_characters_lists(tenant, fake):
    _setup_alice()
    fake.lists[ALICE] = [{"mailing_list_id": 7, "name": "Fleet list"}]
    fake.mailboxes[ALICE] = [_raw(1, recipients=[
        {"recipient_id": 7, "recipient_type": "mailing_list"},
        {"recipient_id": 8, "recipient_type": "mailing_list"},
    ])]
    (mail,) = mail_actions.do_list_mails()["mails"]
    assert [r["name"] for r in mail["recipients"]] == ["Fleet list", "Mailing list 8"]


# ------------------------------------------------------------- archive mode
def test_archived_characters_are_read_from_the_archive_and_never_hit_esi(tenant, fake):
    _setup_alice(archived=True)
    storage.store_mail_headers(ALICE, [_raw(10), _raw(20)])
    result = mail_actions.do_list_mails()
    assert [m["mail_id"] for m in result["mails"]] == [20, 10]
    assert result["mails"][0]["received_by"][0]["archived"] is True
    assert not [c for c in fake.calls if c[0] == "headers"]


def test_mixed_inbox_merges_archived_and_live_characters(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE)
    _share(BOB)
    storage.enable_mail_archive(ALICE)
    storage.store_mail_headers(ALICE, [_raw(10, ts="2026-09-10T00:00:00Z")])
    fake.mailboxes[BOB] = [_raw(20, ts="2026-09-20T00:00:00Z")]
    result = mail_actions.do_list_mails()
    assert [(m["mail_id"], m["received_by"][0]["archived"]) for m in result["mails"]] == [(20, False), (10, True)]


def test_open_archived_mail_uses_the_stored_body_then_fetches_and_stores_a_missing_one(tenant, fake):
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(10), _raw(11)]
    storage.store_mail_headers(ALICE, fake.mailboxes[ALICE])
    storage.store_mail_body(ALICE, 10, "archived body")
    assert mail_actions.do_open_mail(ALICE, 10)["body"] == "archived body"
    assert not [c for c in fake.calls if c[0] == "body"]
    assert mail_actions.do_open_mail(ALICE, 11)["body"] == "Body 11"           # fetched live ...
    assert storage.get_archived_mail(ALICE, 11)["body"] == "Body 11"           # ... and stored


def test_search_covers_archived_characters_only_and_says_so(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE)
    _share(BOB)
    storage.enable_mail_archive(ALICE)
    storage.store_mail_headers(ALICE, [_raw(10, subject="doctrine news")])
    result = mail_actions.do_search_mail("doctrine")
    assert [m["mail_id"] for m in result["mails"]] == [10]
    assert [c["character_id"] for c in result["searched"]] == [ALICE]
    assert [c["character_id"] for c in result["unsearchable"]] == [BOB]
    with pytest.raises(ActionError, match="at least 2"):
        mail_actions.do_search_mail("d")


def test_enabling_the_archive_needs_sharing_and_scope_and_starts_the_backfill(tenant, monkeypatch):
    started = []
    monkeypatch.setattr(mail_archive, "ensure_backfill", lambda cid: started.append(cid) or True)
    _token(ALICE, "Alice")
    with pytest.raises(ActionError, match="not shared with Mail"):
        mail_actions.do_set_mail_archive(ALICE, True)
    _share(ALICE)
    _token(BOB, "Bob", scopes="esi-skills.read_skills.v1")
    _share(BOB)
    with pytest.raises(ActionError, match="re-authorize"):
        mail_actions.do_set_mail_archive(BOB, True)
    status = mail_actions.do_set_mail_archive(ALICE, True)
    assert status["archive_enabled"] is True and started == [ALICE]
    with pytest.raises(ActionError, match="not registered"):
        mail_actions.do_set_mail_archive(424242, True)


def test_disabling_deletes_only_with_confirmation(tenant):
    _setup_alice(archived=True)
    storage.store_mail_headers(ALICE, [_raw(10), _raw(11)])
    with pytest.raises(ActionError, match="Confirm to delete"):
        mail_actions.do_set_mail_archive(ALICE, False)
    assert storage.get_archived_mail(ALICE, 10) is not None          # still there
    out = mail_actions.do_set_mail_archive(ALICE, False, confirm_delete=True)
    assert out["archive_enabled"] is False and out["deleted"]["headers"] == 2
    assert all(_count(t) == 0 for t in _MAIL_TABLES)
    # an empty archive needs no confirmation
    storage.enable_mail_archive(ALICE)
    assert mail_actions.do_set_mail_archive(ALICE, False)["archive_enabled"] is False


def test_status_reports_interrupted_when_the_backfill_thread_is_gone(tenant):
    _setup_alice(archived=True)
    storage.update_mail_archive_state(ALICE, backfill_state="running")
    row = mail_actions.do_mail_archive_status()["characters"][0]
    assert row["archive_enabled"] is True and row["backfill_state"] == "interrupted"
    assert row["shared"] is True and row["reauth_needed"] is False
    storage.update_mail_archive_state(ALICE, backfill_state="done")
    assert mail_actions.do_mail_archive_status()["characters"][0]["backfill_state"] == "done"


def test_status_lists_unshared_characters_as_unable_to_archive(tenant):
    _token(BOB, "Bob")
    row = mail_actions.do_mail_archive_status()["characters"][0]
    assert row["shared"] is False and row["archive_enabled"] is False and row["backfill_state"] == "off"


# ------------------------------------------------------- backfill / refresh
def _tokens():
    return TokenManager(OAUTH_CONFIG)


def test_backfill_pages_through_history_then_fetches_every_body(tenant, fake):
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(i) for i in range(1, 121)]
    result = mail_archive.backfill_character(ALICE, client=fake, tokens=_tokens())
    assert result == {"done": True}
    assert storage.mail_archive_counts(ALICE) == {"headers": 120, "bodies": 120}
    settings = storage.get_mail_archive_settings([ALICE])[ALICE]
    assert settings["backfill_state"] == "done" and settings["headers_complete"] is True
    assert settings["backfill_cursor"] is None
    assert all(c[4] is False for c in fake.calls if c[0] == "headers")        # never through the cache
    assert all(c[3] is False for c in fake.calls if c[0] == "body")


def test_backfill_resumes_from_its_cursor_after_an_error(tenant, fake):
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(i) for i in range(1, 121)]
    fake.header_error_after_pages[ALICE] = 2                     # pages 1-2 fine, page 3 fails
    first = mail_archive.backfill_character(ALICE, client=fake, tokens=_tokens())
    assert first == {"error": "ESI returned HTTP 500"}
    settings = storage.get_mail_archive_settings([ALICE])[ALICE]
    assert settings["backfill_state"] == "error"
    assert settings["backfill_error"] == "ESI returned HTTP 500" and "secret" not in settings["backfill_error"]
    assert settings["backfill_cursor"] == 21 and settings["headers_complete"] is False
    assert storage.mail_archive_counts(ALICE)["headers"] == 100

    fake.header_error_after_pages.clear()
    fake.calls.clear()
    assert mail_archive.backfill_character(ALICE, client=fake, tokens=_tokens()) == {"done": True}
    assert fake.calls[0][:4] == ("headers", ALICE, (), 21)          # resumed, not restarted
    assert storage.mail_archive_counts(ALICE) == {"headers": 120, "bodies": 120}
    assert storage.get_mail_archive_settings([ALICE])[ALICE]["backfill_error"] is None


def test_backfill_stores_an_empty_body_for_a_deleted_mail_instead_of_retrying_forever(tenant, fake):
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(1), _raw(2)]
    fake.body_errors[2] = "HTTP 404 for https://esi/mail/2: gone"
    assert mail_archive.backfill_character(ALICE, client=fake, tokens=_tokens()) == {"done": True}
    assert storage.mail_ids_without_body(ALICE, 10) == []
    assert storage.get_archived_mail(ALICE, 2)["body"] == ""


def test_backfill_aborts_on_other_body_errors_and_records_them_redacted(tenant, fake):
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(1)]
    fake.body_errors[1] = "HTTP 500 for https://esi/mail/1: {\"error\": \"secret-body\"}"
    assert mail_archive.backfill_character(ALICE, client=fake, tokens=_tokens()) == {"error": "ESI returned HTTP 500"}
    assert "secret" not in storage.get_mail_archive_settings([ALICE])[ALICE]["backfill_error"]


def test_backfill_stops_and_writes_nothing_more_once_the_archive_is_deleted(tenant, fake):
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(i) for i in range(1, 121)]

    def delete_after_first_page(cid, page_no):
        if page_no == 2:
            storage.delete_mail_archive(cid)

    fake.on_header_page = delete_after_first_page
    result = mail_archive.backfill_character(ALICE, client=fake, tokens=_tokens())
    assert result == {"stopped": "archive disabled"}
    assert all(_count(t) == 0 for t in _MAIL_TABLES)              # page 2 was refused, nothing re-created


def test_backfill_without_the_mail_scope_records_reauth(tenant, fake):
    _token(ALICE, "Alice", scopes="esi-skills.read_skills.v1")
    _share(ALICE)
    storage.enable_mail_archive(ALICE)
    assert mail_archive.backfill_character(ALICE, client=fake, tokens=_tokens()) == {"error": "reauth_needed"}
    assert storage.get_mail_archive_settings([ALICE])[ALICE]["backfill_state"] == "error"
    assert fake.calls == []


def test_backfill_for_a_character_without_an_archive_is_a_noop(tenant, fake):
    assert mail_archive.backfill_character(ALICE, client=fake, tokens=_tokens()) == {"skipped": "archive not enabled"}
    assert fake.calls == []


def test_refresh_stores_new_mail_and_tracks_read_state_within_the_window(tenant, fake):
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(1, read=False), _raw(2, read=False)]
    fake.labels[ALICE] = {"total_unread_count": 2, "labels": [{"label_id": 1, "name": "Inbox", "unread_count": 2}]}
    fake.lists[ALICE] = [{"mailing_list_id": 7, "name": "Fleet list"}]
    first = mail_archive.refresh_character(ALICE, fake, "esi:1001")
    assert first == {"new_mails": 2, "covered": 2}
    assert [r[1] for r in storage.load_mail_labels(ALICE)] == ["Inbox"]
    assert storage.load_mail_lists(ALICE) == [(7, "Fleet list")]

    fake.mailboxes[ALICE] = [_raw(3), _raw(1, read=True), _raw(2, read=False)]
    second = mail_archive.refresh_character(ALICE, fake, "esi:1001")
    assert second == {"new_mails": 1, "covered": 3}
    assert storage.get_archived_mail(ALICE, 1)["is_read"] is True        # changed in the game
    assert storage.get_mail_archive_settings([ALICE])[ALICE]["last_refresh_at"] is not None


def test_refresh_reads_at_most_the_newest_window_and_leaves_history_to_the_backfill(tenant, fake):
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(i) for i in range(1, 801)]
    result = mail_archive.refresh_character(ALICE, fake, "esi:1001")
    assert result["covered"] == 500
    assert len([c for c in fake.calls if c[0] == "headers"]) == 10
    assert storage.mail_archive_counts(ALICE)["headers"] == 500


def test_do_refresh_resumes_the_backfill_when_history_or_bodies_are_missing(tenant, fake, monkeypatch):
    started = []
    monkeypatch.setattr(mail_archive, "ensure_backfill", lambda cid: started.append(cid) or True)
    _setup_alice(archived=True)
    fake.mailboxes[ALICE] = [_raw(1)]
    out = mail_actions.do_refresh_mail_archive()
    assert out["characters"][0]["new_mails"] == 1 and started == [ALICE]      # bodies still missing


def test_do_refresh_skips_live_characters_and_reports_esi_errors_redacted(tenant, fake):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE)
    _share(BOB)
    storage.enable_mail_archive(BOB)                         # Alice stays live
    fake.header_error_after_pages[BOB] = 0
    out = mail_actions.do_refresh_mail_archive()
    assert [c["character_id"] for c in out["characters"]] == [BOB]
    assert out["characters"][0]["state"] == "error" and out["characters"][0]["detail"] == "ESI returned HTTP 500"


def test_ensure_backfill_starts_one_thread_and_never_a_second_while_it_runs(tenant, monkeypatch):
    import threading
    release, entered = threading.Event(), threading.Event()

    def slow_backfill(character_id, **kw):
        entered.set()
        release.wait(5)

    monkeypatch.setattr(mail_archive, "backfill_character", slow_backfill)
    assert mail_archive.ensure_backfill(ALICE) is True
    assert entered.wait(5)
    assert mail_archive.backfill_alive(ALICE) is True
    assert mail_archive.ensure_backfill(ALICE) is False
    release.set()
    mail_archive._threads[(storage.get_current_tenant(), ALICE)].join(5)
    assert mail_archive.backfill_alive(ALICE) is False
    assert mail_archive.ensure_backfill(ALICE) is True        # a finished thread can be restarted
    mail_archive._threads[(storage.get_current_tenant(), ALICE)].join(5)


# -------------------------------------------------------------------- router
_TENANT = "00000000-0000-0000-0000-000000000cab"
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


def test_char_mail_routes_need_the_char_mail_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    monkeypatch.setattr(mail_actions, "do_list_folders", lambda: {"characters": [], "unread": {}})
    storage.add_tenant_registry_entry(_TENANT, 1, character_name="Some Character")
    storage.set_tool_grant(1, "char_skills", _TENANT)
    assert _client.get("/api/char-mail/folders", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "char_mail", _TENANT)
    assert _client.get("/api/char-mail/folders", cookies=_cookie()).status_code == 200


def test_router_passes_cursors_and_filters_and_rejects_bad_cursors(monkeypatch):
    seen = {}

    def fake_list(label_id=None, character_id=None, cursors=None):
        seen.update(label_id=label_id, character_id=character_id, cursors=cursors)
        return {"mails": [], "next_cursors": {}, "characters": []}

    monkeypatch.setattr(mail_actions, "do_list_mails", fake_list)
    resp = _client.get("/api/char-mail/mails", params={
        "label_id": 1, "character_id": 5, "cursors": json.dumps({"5": 99}),
    })
    assert resp.status_code == 200
    assert seen == {"label_id": 1, "character_id": 5, "cursors": {"5": 99}}
    assert _client.get("/api/char-mail/mails", params={"cursors": "not json"}).status_code == 400
    assert _client.get("/api/char-mail/mails", params={"cursors": "[1]"}).status_code == 400


def test_router_converts_action_errors_and_passes_archive_flags(monkeypatch):
    def boom(**kw):
        raise ActionError("nope")

    monkeypatch.setattr(mail_actions, "do_open_mail", boom)
    resp = _client.get("/api/char-mail/mails/1/2")
    assert resp.status_code == 400 and resp.json()["detail"] == "nope"

    seen = {}
    monkeypatch.setattr(mail_actions, "do_set_mail_archive", lambda **kw: seen.update(kw) or {"ok": True})
    _client.post("/api/char-mail/archive", json={"character_id": 5, "enabled": False, "confirm_delete": True})
    assert seen == {"character_id": 5, "enabled": False, "confirm_delete": True}
    monkeypatch.setattr(mail_actions, "do_refresh_mail_archive", lambda **kw: seen.update(kw) or {"characters": []})
    _client.post("/api/char-mail/archive/refresh", json={})
    assert seen["character_id"] is None

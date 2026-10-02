"""Discord alerts job body: skill queue (snapshot check + one live re-check,
dedupe, backoff), mail (baseline, count-only, content opt-in, failures), the
demand-only ESI sync and the scheduler hook."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import scheduler, storage, tenant_eligibility
from eve_trader.alerts import actions as aa
from eve_trader.alerts import discord_client, runner
from eve_trader.auth import TokenRecord
from eve_trader.config import ACCESS_CONFIG
from eve_trader.esi_client import ESIError

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_refining import _apply_refining_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

ALICE = 3001
UID = "123456789012345678"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
SCOPES = "esi-skills.read_skillqueue.v1 esi-mail.read_mail.v1"
_TABLES = ("alert_state", "alert_subscriptions", "alert_destinations", "esi_sharing", "esi_freshness",
           "character_skillqueue", "tenant_tokens")


class FakeClient:
    def __init__(self, queue=None, headers=None, bodies=None):
        self.queue, self.headers, self.bodies = queue, headers or [], bodies or {}
        self.queue_error = self.mail_error = False
        self.calls: list[str] = []

    def character_skillqueue(self, cid, role):
        self.calls.append("queue")
        if self.queue_error:
            raise ESIError("HTTP 500")
        return self.queue

    def character_mail_headers(self, cid, role, labels=None, last_mail_id=None, cache=True):
        self.calls.append("headers")
        if self.mail_error:
            raise ESIError("HTTP 500")
        return self.headers

    senders: dict = {}
    names_error = False

    def resolve_names_cached(self, ids):
        self.calls.append(f"names{sorted(ids)}")
        if self.names_error and len(ids) > 1:
            raise ESIError("HTTP 404")
        return {i: self.senders[i] for i in ids if i in self.senders}

    def character_mail_body(self, cid, role, mail_id, cache=True):
        self.calls.append(f"body{mail_id}")
        return {"body": self.bodies[mail_id]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    pg_helpers.wipe_tables(*_TABLES)
    sent: list[str] = []
    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: sent.append(text))
    monkeypatch.setattr(runner.discord_client, "send_dm", discord_client.send_dm)
    yield sent
    pg_helpers.wipe_tables(*_TABLES)


def _setup(kinds=("skillqueue", "mail")):
    role = f"esi:{ALICE}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=ALICE, character_name="Alice", access_token="a", refresh_token="r",
        expires_at=9999999999.0, scopes=SCOPES)))
    storage.set_alert_destination(UID)
    with storage.connect() as conn:
        for kind in kinds:
            conn.execute("INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                         "VALUES ('character', ?, ?, 'char_alerts') ON CONFLICT DO NOTHING", (ALICE, kind))


def _queue_snapshot(hours):
    finish = (NOW + timedelta(hours=hours)).isoformat()
    storage.replace_character_skillqueue(ALICE, [(0, 1, 5, None, finish, None, None, None)])
    storage.upsert_esi_freshness("character", ALICE, "skillqueue", success=True)


def _live(hours):
    return [{"queue_position": 0, "skill_id": 1, "finished_level": 5,
             "finish_date": (NOW + timedelta(hours=hours)).isoformat()}]


def _run(client, **kw):
    return runner.run_for_tenant(now=kw.pop("now", NOW), client=client, poll_minutes=kw.pop("poll", 10), **kw)


@pytest.fixture(autouse=True)
def _no_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(runner.esi_orchestrator, "do_sync_due", lambda **kw: calls.append(kw))
    return calls


# -------------------------------------------------------------- skill queue
def test_nothing_runs_without_opt_in(tenant, _env, _no_sync):
    _setup()
    assert _run(FakeClient())["ran"] is False and _env == [] and _no_sync == []


def test_warning_needs_a_synced_snapshot_and_a_live_confirmation(tenant, _env):
    _setup()
    aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    fake = FakeClient(queue=_live(3))
    _run(fake)                                   # never synced: an empty snapshot must not read as an empty queue
    assert fake.calls == [] and _env == []
    _queue_snapshot(3)
    _run(fake)
    assert fake.calls == ["queue"] and len(_env) == 1 and "ends in 3 h" in _env[0]
    _run(fake, now=NOW + timedelta(hours=1))     # same finish date: announced once
    assert len(_env) == 1 and fake.calls == ["queue"]


def test_an_extended_queue_produces_no_false_alarm(tenant, _env):
    _setup()
    aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    _queue_snapshot(3)
    fake = FakeClient(queue=_live(60))           # user extended it since the snapshot
    _run(fake)
    assert _env == [] and fake.calls == ["queue"]
    _run(fake, now=NOW + timedelta(minutes=5))   # inside the backoff: no second live call
    assert fake.calls == ["queue"]


def test_failed_live_check_or_discord_delivery_backs_off_and_keeps_the_cursor(tenant, _env, monkeypatch):
    _setup()
    aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    _queue_snapshot(3)
    fake = FakeClient(queue=_live(3))
    fake.queue_error = True
    _run(fake)
    assert _env == [] and storage.get_alert_state(ALICE, "skillqueue_empty")[3] is not None
    fake.queue_error = False
    _run(fake, now=NOW + timedelta(minutes=5))   # still backing off
    assert fake.calls == ["queue"]

    def boom(uid, text):
        raise discord_client.DiscordRateLimited(3)
    monkeypatch.setattr(discord_client, "send_dm", boom)
    _run(fake, now=NOW + timedelta(minutes=20))
    state = storage.get_alert_state(ALICE, "skillqueue_empty")
    assert state[1] is None and state[2] is None  # no key stored: it will be announced once Discord is back
    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: _env.append(text))
    _run(fake, now=NOW + timedelta(minutes=40))
    assert len(_env) == 1 and storage.get_alert_state(ALICE, "skillqueue_empty")[1].startswith("ending:")


def test_queue_sync_is_restricted_to_the_alert_demand(tenant, _no_sync):
    _setup()
    aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    _run(FakeClient())
    assert _no_sync[0]["granted_tools"] == {"char_alerts"}
    assert _no_sync[0]["demand"] == {("character", ALICE, "skillqueue")}


# --------------------------------------------------------------------- mail
def _h(i, read=False, subject="s"):
    return {"mail_id": i, "is_read": read, "subject": subject, "from": 1, "timestamp": "2026-09-29T11:00:00Z"}


def test_mail_baseline_then_count_only_alert(tenant, _env):
    _setup()
    aa.do_set_subscription(ALICE, "mail_new", True)
    fake = FakeClient(headers=[_h(5), _h(9)])
    _run(fake)                                   # baseline: no alert for old mail
    assert _env == [] and storage.get_alert_state(ALICE, "mail_new")[0] == 9
    fake.headers = [_h(11, subject="SECRET"), _h(9), _h(10)]
    _run(fake, now=NOW + timedelta(minutes=5))   # inside the poll interval: nothing
    assert fake.calls == ["headers"]
    _run(fake, now=NOW + timedelta(minutes=11))
    assert _env[0].startswith("Alice: 2 new EVE mails.") and "SECRET" in _env[0]   # subject is metadata now
    assert storage.get_alert_state(ALICE, "mail_new")[0] == 11
    assert not any(c.startswith("body") for c in fake.calls)


def test_an_empty_inbox_baseline_still_catches_the_first_mail(tenant, _env):
    _setup()
    aa.do_set_subscription(ALICE, "mail_new", True)
    fake = FakeClient(headers=[])
    _run(fake)
    assert storage.get_alert_state(ALICE, "mail_new")[0] == 0
    fake.headers = [_h(4)]
    _run(fake, now=NOW + timedelta(minutes=11))
    assert _env[0].startswith("Alice: 1 new EVE mail.")


def test_sender_names_are_resolved_and_a_bad_id_does_not_lose_the_others(tenant, _env):
    _setup()
    aa.do_set_subscription(ALICE, "mail_new", True)
    fake = FakeClient(headers=[_h(1)])
    fake.senders = {7: "Bob"}
    fake.names_error = True                      # the batch 404s: fall back to one call per id
    _run(fake)
    fake.headers = [{**_h(3, subject="Hi"), "from": 7}, {**_h(2, subject="List"), "from": 8}, _h(1)]
    _run(fake, now=NOW + timedelta(minutes=11))
    assert "- Bob: Hi" in _env[0] and "- unknown sender: List" in _env[0]
    assert "names[7, 8]" in fake.calls and "names[7]" in fake.calls
    assert "body" not in _env[0]


def test_content_opt_in_fetches_cleaned_bodies(tenant, _env):
    _setup()
    aa.do_set_subscription(ALICE, "mail_new", True, include_content=True)
    fake = FakeClient(headers=[_h(1)], bodies={2: "<font size=12>hello   <b>there</b></font>"})
    _run(fake)
    fake.headers = [_h(2, subject="Hi"), _h(1)]
    _run(fake, now=NOW + timedelta(minutes=11))
    assert "Hi" in _env[0] and "hello there" in _env[0] and "<" not in _env[0]


def test_failed_mail_poll_or_send_does_not_advance_the_cursor(tenant, _env, monkeypatch):
    _setup()
    aa.do_set_subscription(ALICE, "mail_new", True)
    fake = FakeClient(headers=[_h(1)])
    _run(fake)
    fake.headers = [_h(2), _h(1)]
    fake.mail_error = True
    _run(fake, now=NOW + timedelta(minutes=11))
    assert storage.get_alert_state(ALICE, "mail_new")[0] == 1
    fake.mail_error = False
    monkeypatch.setattr(discord_client, "send_dm", lambda *a: (_ for _ in ()).throw(discord_client.DiscordError("x")))
    _run(fake, now=NOW + timedelta(minutes=22))
    assert storage.get_alert_state(ALICE, "mail_new")[0] == 1 and _env == []
    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: _env.append(text))
    _run(fake, now=NOW + timedelta(minutes=33))
    assert _env[0].startswith("Alice: 1 new EVE mail.")


def test_revoked_sharing_stops_alerts_immediately(tenant, _env):
    _setup()
    aa.do_set_subscription(ALICE, "mail_new", True)
    fake = FakeClient(headers=[_h(1)])
    _run(fake)
    with storage.connect() as conn:
        conn.execute("DELETE FROM esi_sharing")
    fake.headers = [_h(2), _h(1)]
    _run(fake, now=NOW + timedelta(minutes=11))
    assert _env == [] and fake.calls == ["headers"]


# ------------------------------------------------------------- in-flight guard
def test_a_second_concurrent_run_for_the_same_tenant_is_skipped_not_duplicated(tenant, _env, monkeypatch):
    """Confirmed real gap (code review 2026-10-01): scheduler._run_job abandons
    a job thread after its timeout rather than waiting for it, so a slow run
    (ESI/Discord both near their own timeouts) could still be in flight when
    the next 5-minute tick starts a second one for the same tenant - both would
    read alert_state before either writes it and could send the same alert
    twice. A second run_for_tenant() while the first is still inside its
    critical section must return immediately (skipped, not a duplicate send),
    and the first must still complete normally once unblocked."""
    import threading

    _setup()
    aa.do_set_subscription(ALICE, "mail_new", True)
    _run(FakeClient(headers=[_h(1)]))    # baseline: cursor=1, nothing sent yet
    assert _env == []

    entered = threading.Event()
    release = threading.Event()
    real_names = runner._names

    def blocking_names():
        entered.set()
        assert release.wait(5), "first run was never released - guard held forever?"
        return real_names()

    monkeypatch.setattr(runner, "_names", blocking_names)

    result: dict = {}
    later = NOW + timedelta(minutes=11)  # past the poll backoff from the baseline run above
    first = threading.Thread(
        target=storage.with_current_tenant(
            lambda: result.update(first=_run(FakeClient(headers=[_h(2)]), now=later))
        ),
    )
    first.start()
    assert entered.wait(5), "first run never reached its critical section"

    second = _run(FakeClient(headers=[_h(2)]), now=later)  # same tenant, still in-process

    release.set()
    first.join(5)
    assert not first.is_alive()

    assert second == {"subscriptions": 0, "ran": False, "skipped": "in_flight"}
    assert result["first"]["ran"] is True
    assert _env == ["Alice: 1 new EVE mail.\n- unknown sender: s"]  # sent exactly once, not twice


# ---------------------------------------------------------------- scheduler
def test_scheduler_runs_alerts_only_when_the_operator_switch_is_on(tenant, monkeypatch):
    _setup()
    aa.do_set_subscription(ALICE, "mail_new", True)
    ran = []
    monkeypatch.setattr(scheduler, "_run_job", lambda tid, name, fn: ran.append((tid, name)))
    monkeypatch.setattr(storage, "list_tenants", lambda: [(tenant, "t", None)])
    monkeypatch.setattr(scheduler.SCHEDULER_OPERATOR_CONFIG, "alerts_job_enabled", False)
    scheduler._check_and_run_alerts_job()
    assert ran == []
    monkeypatch.setattr(scheduler.SCHEDULER_OPERATOR_CONFIG, "alerts_job_enabled", True)
    scheduler._check_and_run_alerts_job()
    assert ran == [(tenant, "alerts")]


def test_alerts_run_without_master_switch_and_for_inactive_tenants(tenant, monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "_run_job", lambda tid, name, fn: ran.append(name))
    monkeypatch.setattr(scheduler, "_master_enabled", lambda: False)
    monkeypatch.setattr(scheduler.SCHEDULER_OPERATOR_CONFIG, "alerts_job_enabled", True)
    monkeypatch.setattr(scheduler, "_check_and_run_alerts_job", lambda: ran.append("alerts-hook"))
    scheduler._check_and_run_due_jobs()
    assert ran == ["alerts-hook"]


def test_alerts_allowed_needs_registration_no_suspension_and_the_grant(tenant, monkeypatch, _apply_admin_schema):
    assert tenant_eligibility.alerts_allowed(tenant) is True          # gate off
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    assert tenant_eligibility.alerts_allowed(tenant) is False         # no registry entry
    storage.add_tenant_registry_entry(tenant, 9001, character_name="X")
    assert tenant_eligibility.alerts_allowed(tenant) is False         # no grant
    storage.set_tool_grant(9001, "char_alerts", tenant)
    assert tenant_eligibility.alerts_allowed(tenant) is True
    with storage.connect_unscoped() as conn:
        conn.execute("UPDATE tenant_registry_entries SET access_suspended = TRUE WHERE tenant_id = ?", (tenant,))
    assert tenant_eligibility.alerts_allowed(tenant) is False
    with storage.connect_unscoped() as conn:
        conn.execute("DELETE FROM tool_grants WHERE tenant_id = ?", (tenant,))
        conn.execute("DELETE FROM tenant_registry_entries WHERE tenant_id = ?", (tenant,))

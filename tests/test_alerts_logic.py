"""Discord alerts: pure decision logic and the Discord client (no Postgres)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader.alerts import discord_client as dc
from eve_trader.alerts import logic
from eve_trader.alerts.config import DiscordConfig

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _iso(hours):
    return (NOW + timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------- skill queue
def test_long_queue_is_quiet():
    assert logic.skillqueue_decision([_iso(30)], NOW, 12, None, "Alice") is None


def test_queue_within_lead_time_warns_once_per_finish_date():
    d = logic.skillqueue_decision([_iso(3), _iso(10)], NOW, 12, None, "Alice")
    assert d and d.key == f"ending:{_iso(10)}" and "ends in 10 h" in d.message
    assert logic.skillqueue_decision([_iso(3), _iso(10)], NOW, 12, d.key, "Alice") is None


def test_extending_the_queue_re_arms_the_warning():
    old = logic.skillqueue_decision([_iso(10)], NOW, 12, None, "Alice")
    later = NOW + timedelta(hours=1)
    # queue was extended past the lead window: quiet; then it shrinks again with a new finish date
    assert logic.skillqueue_decision([_iso(40)], later, 12, old.key, "Alice") is None
    new = logic.skillqueue_decision([_iso(11.5)], later, 12, old.key, "Alice")
    assert new and new.key != old.key


def test_empty_paused_and_ended_queues():
    assert logic.skillqueue_decision([], NOW, 12, None, "Alice").key == "empty"
    assert logic.skillqueue_decision([None], NOW, 12, None, "Alice").key == "paused"
    ended = logic.skillqueue_decision([_iso(-1)], NOW, 12, None, "Alice")
    assert ended.key.startswith("ended:") and "run empty" in ended.message
    assert logic.skillqueue_decision([], NOW, 12, "empty", "Alice") is None


def test_short_remaining_time_is_shown_in_minutes():
    d = logic.skillqueue_decision([_iso(0.5)], NOW, 12, None, "Alice")
    assert "30 min" in d.message


# ----------------------------------------------------------------------- mail
def _h(i, read=False, subject="s"):
    return {"mail_id": i, "is_read": read, "subject": subject}


def test_first_run_is_a_baseline_and_sends_nothing():
    r = logic.mail_decision([_h(5), _h(9)], None, False, "Alice")
    assert r.baseline and r.newest_id == 9 and r.decision is None


def test_baseline_of_an_empty_inbox_stays_unset():
    r = logic.mail_decision([], None, False, "Alice")
    assert r.baseline and r.newest_id is None


def test_new_mail_message_carries_only_the_count_without_content_opt_in():
    r = logic.mail_decision([_h(11, subject="SECRET"), _h(12), _h(3)], 10, False, "Alice")
    assert r.decision.message == "Alice: 2 new EVE mails."
    assert "SECRET" not in r.decision.message and r.newest_id == 12
    assert logic.mail_decision([_h(11)], 10, False, "Alice").decision.message == "Alice: 1 new EVE mail."


def test_read_or_old_mail_does_not_alert_but_advances_the_cursor():
    r = logic.mail_decision([_h(11, read=True), _h(4)], 10, False, "Alice")
    assert r.decision is None and r.newest_id == 11


def test_content_opt_in_lists_subjects_and_bodies_truncated():
    r = logic.mail_decision(
        [_h(i, subject=f"subj{i}") for i in range(11, 18)], 10, True, "Alice", bodies={11: "hello"},
    )
    text = r.decision.message
    assert "subj11" in text and "hello" in text and "and 2 more" in text
    assert logic.mail_decision([_h(11, subject="x" * 500)], 10, True, "A").decision.message.count("x") == 100


# -------------------------------------------------------------------- client
CFG = DiscordConfig(bot_token="TOK", client_id="cid", client_secret="sec", redirect_uri="https://x/cb")


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


def test_valid_user_id():
    assert dc.valid_user_id("123456789012345678")
    assert not dc.valid_user_id("12ab") and not dc.valid_user_id(123456789012345678) and not dc.valid_user_id("")


def test_authorize_url_requests_identify_only():
    url = dc.authorize_url("STATE", CFG)
    assert url.startswith(dc.AUTHORIZE_URL) and "scope=identify" in url and "state=STATE" in url


def test_send_dm_disables_mentions_and_truncates(monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        return _Resp(200, {"id": "999"})
    monkeypatch.setattr(dc.requests, "post", post)
    dc.send_dm("123456789012345678", "@everyone " + "x" * 5000, CFG)
    assert calls[0][0].endswith("/users/@me/channels") and calls[0][1]["headers"]["Authorization"] == "Bot TOK"
    body = calls[1][1]["json"]
    assert calls[1][0].endswith("/channels/999/messages")
    assert body["allowed_mentions"] == {"parse": []} and len(body["content"]) == dc.MAX_CONTENT_CHARS


def test_send_dm_error_mapping(monkeypatch):
    monkeypatch.setattr(dc.requests, "post", lambda *a, **k: _Resp(429, {"retry_after": 7}))
    with pytest.raises(dc.DiscordRateLimited) as e:
        dc.send_dm("123456789012345678", "hi", CFG)
    assert e.value.retry_after == 7
    seq = iter([_Resp(200, {"id": "1"}), _Resp(403)])
    monkeypatch.setattr(dc.requests, "post", lambda *a, **k: next(seq))
    with pytest.raises(dc.DiscordDMBlocked):
        dc.send_dm("123456789012345678", "hi", CFG)


def test_send_dm_needs_a_token_and_a_valid_id():
    with pytest.raises(dc.DiscordError):
        dc.send_dm("123456789012345678", "hi", DiscordConfig("", "", "", ""))
    with pytest.raises(dc.DiscordError):
        dc.send_dm("nope", "hi", CFG)


def test_error_messages_never_contain_the_token(monkeypatch):
    monkeypatch.setattr(dc.requests, "post", lambda *a, **k: _Resp(500))
    with pytest.raises(dc.DiscordError) as e:
        dc.send_dm("123456789012345678", "hi", CFG)
    assert "TOK" not in str(e.value)


def test_link_exchange_returns_the_user_id(monkeypatch):
    monkeypatch.setattr(dc.requests, "post", lambda *a, **k: _Resp(200, {"access_token": "at"}))
    monkeypatch.setattr(dc.requests, "get", lambda *a, **k: _Resp(200, {"id": "123456789012345678"}))
    assert dc.exchange_code_for_user_id("code", CFG) == "123456789012345678"
    monkeypatch.setattr(dc.requests, "get", lambda *a, **k: _Resp(200, {"id": "bad"}))
    with pytest.raises(dc.DiscordError):
        dc.exchange_code_for_user_id("code", CFG)

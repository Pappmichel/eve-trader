"""Discord alerts: storage, actions (opt-in, sharing, link, delivery re-check),
tenant isolation and the route grant."""
from __future__ import annotations

from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.alerts import actions as aa
from eve_trader.alerts import discord_client
from eve_trader.alerts.config import DiscordConfig
from eve_trader.api.app import create_app
from eve_trader.auth import TokenRecord
from eve_trader.config import OAUTH_CONFIG

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant, tenant_pair,
)
from .test_char_info import _TENANT, _cookie, _enable_gate, _gate_tables, _provision  # noqa: F401
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_refining import _apply_refining_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

ALICE = 2001
UID = "123456789012345678"
CFG = DiscordConfig("TOK", "cid", "sec", "https://x/cb")
_TABLES = ("alert_state", "alert_subscriptions", "alert_destinations", "esi_sharing", "tenant_tokens")
_client = TestClient(create_app())


@pytest.fixture(autouse=True)
def _wipe(monkeypatch):
    pg_helpers.wipe_tables(*_TABLES)
    monkeypatch.setattr(aa, "load_config", lambda: CFG)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    yield
    pg_helpers.wipe_tables(*_TABLES)


def _token(cid=ALICE, name="Alice", scopes="esi-skills.read_skillqueue.v1 esi-mail.read_mail.v1"):
    role = f"esi:{cid}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=cid, character_name=name, access_token="a", refresh_token="r",
        expires_at=9999999999.0, scopes=scopes,
    )))


def _share(kind, cid=ALICE, tool="char_alerts"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING", (cid, kind, tool))


def test_default_settings_are_all_off(tenant):
    _token()
    s = aa.do_get_alert_settings()
    assert s["linked"] is False and s["bot_configured"] and s["link_configured"]
    alerts = s["characters"][0]["alerts"]
    assert alerts["skillqueue_empty"] == {"shared": False, "enabled": False, "include_content": False, "lead_hours": 12}
    assert alerts["mail_new"]["enabled"] is False


def test_enabling_needs_a_link_and_sharing(tenant):
    _token()
    with pytest.raises(ActionError, match="Link your Discord"):
        aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    storage.set_alert_destination(UID)
    with pytest.raises(ActionError, match="Share skillqueue"):
        aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    _share("skillqueue")
    out = aa.do_set_subscription(ALICE, "skillqueue_empty", True, lead_hours=6)
    assert out["enabled"] and out["lead_hours"] == 6
    assert aa.do_get_alert_settings()["characters"][0]["alerts"]["skillqueue_empty"]["shared"] is True


def test_validation(tenant):
    storage.set_alert_destination(UID)
    for bad in (dict(alert_type="nope", enabled=False), dict(alert_type="skillqueue_empty", enabled=False, lead_hours=0),
                dict(alert_type="skillqueue_empty", enabled=False, lead_hours=169),
                dict(alert_type="skillqueue_empty", enabled=False, include_content=True)):
        with pytest.raises(ActionError):
            aa.do_set_subscription(ALICE, **bad)
    with pytest.raises(ActionError):
        aa.do_set_subscription(-1, "mail_new", False)


def test_fresh_opt_in_resets_the_baseline(tenant):
    storage.set_alert_destination(UID)
    _share("mail")
    aa.do_set_subscription(ALICE, "mail_new", True, include_content=True)
    storage.save_alert_state(ALICE, "mail_new", last_seen_mail_id=50, last_key="k", sent=True)
    aa.do_set_subscription(ALICE, "mail_new", True)          # still on: state kept
    assert storage.get_alert_state(ALICE, "mail_new")[0] == 50
    aa.do_set_subscription(ALICE, "mail_new", False)
    aa.do_set_subscription(ALICE, "mail_new", True)          # switched back on: clean baseline
    assert storage.get_alert_state(ALICE, "mail_new") is None


def test_alert_state_keeps_the_cursor_on_a_failed_attempt(tenant):
    storage.save_alert_state(ALICE, "mail_new", last_seen_mail_id=7, last_key="a", sent=True)
    storage.save_alert_state(ALICE, "mail_new")               # failed attempt: no cursor change
    seen, key, sent_at, attempt_at = storage.get_alert_state(ALICE, "mail_new")
    assert (seen, key) == (7, "a") and sent_at and attempt_at


def test_unlink_switches_every_subscription_off(tenant):
    _share("skillqueue")
    storage.set_alert_destination(UID)
    aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    assert aa.do_unlink_discord() == {"unlinked": True}
    assert storage.get_alert_destination() is None
    assert storage.get_alert_subscription(ALICE, "skillqueue_empty")[0] is False


def test_link_flow(tenant, monkeypatch):
    url = aa.do_start_discord_link()["url"]
    state = url.split("state=")[1].split("&")[0]
    monkeypatch.setattr(discord_client, "exchange_code_for_user_id", lambda code: UID)
    with pytest.raises(ActionError, match="expired or is invalid"):
        aa.do_finish_discord_link("c", "garbage")
    assert aa.do_finish_discord_link("c", state) == {"linked": True}
    assert storage.get_alert_destination() == UID


def test_link_state_from_another_tenant_is_rejected(tenant_pair, monkeypatch):
    a, b = tenant_pair
    with storage.tenant_context(a):
        state = aa.do_start_discord_link()["url"].split("state=")[1].split("&")[0]
    monkeypatch.setattr(discord_client, "exchange_code_for_user_id", lambda code: UID)
    with storage.tenant_context(b):
        with pytest.raises(ActionError, match="different session"):
            aa.do_finish_discord_link("c", state)
        assert storage.get_alert_destination() is None


def test_tenants_are_isolated(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        storage.set_alert_destination(UID)
        storage.upsert_alert_subscription(ALICE, "mail_new", True, False, 12)
    with storage.tenant_context(b):
        assert storage.get_alert_destination() is None
        assert storage.list_alert_subscriptions() == []


def test_link_unavailable_without_operator_config(tenant, monkeypatch):
    monkeypatch.setattr(aa, "load_config", lambda: DiscordConfig("", "", "", ""))
    with pytest.raises(ActionError, match="not configured"):
        aa.do_start_discord_link()


def test_test_message(tenant, monkeypatch):
    sent = []
    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: sent.append((uid, text)))
    with pytest.raises(ActionError, match="Link your Discord"):
        aa.do_send_test_message()
    storage.set_alert_destination(UID)
    assert aa.do_send_test_message() == {"sent": True} and sent[0][0] == UID

    def boom(uid, text):
        raise discord_client.DiscordError("Discord send message failed (HTTP 500).")
    monkeypatch.setattr(discord_client, "send_dm", boom)
    with pytest.raises(ActionError, match="HTTP 500"):
        aa.do_send_test_message()


def test_deliver_rechecks_optin_sharing_and_token_at_send_time(tenant, monkeypatch):
    sent = []
    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: sent.append(text))
    _token()
    _share("skillqueue")
    storage.set_alert_destination(UID)
    assert aa.deliver(ALICE, "skillqueue_empty", "m") is False          # no opt-in yet
    aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    assert aa.deliver(ALICE, "skillqueue_empty", "m") is True and sent == ["m"]
    with storage.connect() as conn:                                      # sharing revoked mid-run
        conn.execute("DELETE FROM esi_sharing")
    assert aa.deliver(ALICE, "skillqueue_empty", "m") is False and len(sent) == 1
    _share("skillqueue")
    with storage.connect() as conn:                                      # token gone
        conn.execute("DELETE FROM tenant_tokens")
    assert aa.deliver(ALICE, "skillqueue_empty", "m") is False and len(sent) == 1


def test_deliver_propagates_discord_failures(tenant, monkeypatch):
    def boom(uid, text):
        raise discord_client.DiscordRateLimited(3)
    monkeypatch.setattr(discord_client, "send_dm", boom)
    _token()
    _share("skillqueue")
    storage.set_alert_destination(UID)
    aa.do_set_subscription(ALICE, "skillqueue_empty", True)
    with pytest.raises(discord_client.DiscordRateLimited):
        aa.deliver(ALICE, "skillqueue_empty", "m")


def test_routes_need_the_char_alerts_grant(monkeypatch, _gate_tables, _apply_admin_schema):
    _enable_gate(monkeypatch)
    _provision(tools=("characters", "char_info"))
    assert _client.get("/api/char-alerts/settings", cookies=_cookie()).status_code == 403
    storage.set_tool_grant(1, "char_alerts", _TENANT)
    assert _client.get("/api/char-alerts/settings", cookies=_cookie()).status_code == 200


def test_router_converts_action_errors_to_400(monkeypatch):
    monkeypatch.setattr(aa, "do_send_test_message", lambda: (_ for _ in ()).throw(ActionError("nope")))
    resp = _client.post("/api/char-alerts/test")
    assert resp.status_code == 400 and resp.json()["detail"] == "nope"


def test_callback_redirects_with_the_outcome(monkeypatch):
    monkeypatch.setattr(aa, "do_finish_discord_link", lambda code, state: {"linked": True})
    r = _client.get("/api/char-alerts/discord/callback?code=c&state=s", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].endswith("/character-management/alerts?discord=linked")
    r = _client.get("/api/char-alerts/discord/callback?error=access_denied", follow_redirects=False)
    assert r.headers["location"].endswith("discord=cancelled")
    monkeypatch.setattr(aa, "do_finish_discord_link", lambda code, state: (_ for _ in ()).throw(ActionError("x")))
    r = _client.get("/api/char-alerts/discord/callback?code=c&state=s", follow_redirects=False)
    assert r.headers["location"].endswith("discord=error")

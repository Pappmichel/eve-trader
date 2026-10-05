"""PI phase 4 on Postgres: colony snapshot, accessor, calibration samples, PI
alert state (RLS) and the alerts runner for the PI alert types."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.alerts import actions as aa
from eve_trader.alerts import discord_client, logic, runner
from eve_trader.auth import TokenRecord
from eve_trader.esi_client import ESIError
from eve_trader.esi_data import read_esi

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant, tenant_pair,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_refining import _apply_refining_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

ALICE = 4001
UID = "123456789012345678"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
SCOPE = "esi-planets.manage_planets.v1"
_TABLES = ("character_pi_colonies", "pi_yield_samples", "pi_alert_state", "alert_state", "alert_subscriptions",
           "alert_destinations", "esi_sharing", "esi_freshness", "tenant_tokens")
_LAYOUT = {"pins": [{"pin_id": 1, "type_id": 2524}], "links": [], "routes": []}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    pg_helpers.wipe_tables(*_TABLES)
    sent: list[str] = []
    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: sent.append(text))
    monkeypatch.setattr(runner.discord_client, "send_dm", discord_client.send_dm)
    monkeypatch.setattr(runner.esi_orchestrator, "do_sync_due", lambda **kw: None)
    runner._pi_attempts.clear()
    yield sent
    pg_helpers.wipe_tables(*_TABLES)


def _share(cid, kind, tool):
    with storage.connect() as conn:
        conn.execute("INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                     "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING", (cid, kind, tool))


def _row(planet_id, last_update="2026-10-04T10:00:00+00:00"):
    return (planet_id, "barren", 30000142, 5, 1, last_update, _LAYOUT)


# ----------------------------------------------------------------- colonies snapshot
def test_colonies_round_trip_and_replace(tenant):
    storage.replace_character_pi_colonies(ALICE, [_row(11), _row(12)])
    rows = storage.load_character_pi_colonies([ALICE])
    assert [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows] == [
        (ALICE, 11, "barren", 30000142, 5, 1), (ALICE, 12, "barren", 30000142, 5, 1)]
    assert rows[0][6].startswith("2026-10-04T10:00:00") and rows[0][7] == _LAYOUT
    storage.replace_character_pi_colonies(ALICE, [_row(13)])
    assert [r[1] for r in storage.load_character_pi_colonies([ALICE])] == [13]
    storage.replace_character_pi_colonies(ALICE, [])
    assert storage.load_character_pi_colonies([ALICE]) == []
    assert storage.load_character_pi_colonies([]) == []


def test_colonies_are_tenant_isolated_and_clearable(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        storage.replace_character_pi_colonies(ALICE, [_row(11)])
    with storage.tenant_context(b):
        assert storage.load_character_pi_colonies([ALICE]) == []
    with storage.tenant_context(a):
        storage.delete_owner_snapshot_rows("character_pi_colonies", owner_character_id=ALICE)
        assert storage.load_character_pi_colonies([ALICE]) == []


def test_read_esi_planets_is_gated_by_sharing(tenant):
    storage.replace_character_pi_colonies(ALICE, [_row(11)])
    assert read_esi("planets", "pi", owner_type="character") == []
    _share(ALICE, "planets", "pi")
    (r,) = read_esi("planets", "pi", owner_type="character")
    assert r["owner_id"] == ALICE and r["planet_id"] == 11 and r["layout"] == _LAYOUT
    assert r["upgrade_level"] == 5 and r["planet_type"] == "barren"
    assert read_esi("planets", "char_alerts") == []                   # its own sharing row


# ----------------------------------------------------------------- samples
def _sample(pin, per_head=10.0, **kw):
    return {"character_id": ALICE, "planet_id": 11, "pin_id": pin, "install_time": NOW, "p0_type_id": 2272,
            "planet_type_id": 2016, "security": 0.9, "heads": 4, "program_hours": 72.0,
            "per_head_per_hour": per_head, **kw}


def test_yield_samples_upsert_and_isolation(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        storage.upsert_pi_yield_samples([_sample(1), _sample(2, 12.0)])
        storage.upsert_pi_yield_samples([_sample(1, 11.0)])           # same key: updated, not duplicated
        got = {s["pin_id"]: s for s in storage.list_pi_yield_samples()}
        assert set(got) == {1, 2} and got[1]["per_head_per_hour"] == 11.0
        assert got[2]["security"] == 0.9 and got[2]["heads"] == 4
    with storage.tenant_context(b):
        assert storage.list_pi_yield_samples() == []
    storage.upsert_pi_yield_samples([])


def test_pi_alert_state_round_trip(tenant):
    storage.set_pi_alert_state(ALICE, 11, logic.PI_PAD_FULL, "k1", at=NOW)
    storage.set_pi_alert_state(ALICE, 11, logic.PI_PAD_FULL, "k2", at=NOW)
    storage.set_pi_alert_state(ALICE, 12, logic.PI_INPUTS_EMPTY, "k3", at=NOW)
    states = storage.get_pi_alert_states(ALICE)
    assert states[(11, logic.PI_PAD_FULL)][0] == "k2" and states[(12, logic.PI_INPUTS_EMPTY)][0] == "k3"
    storage.reset_pi_alert_state(ALICE, logic.PI_PAD_FULL)
    assert list(storage.get_pi_alert_states(ALICE)) == [(12, logic.PI_INPUTS_EMPTY)]
    storage.delete_alert_data_for_character(ALICE)
    assert storage.get_pi_alert_states(ALICE) == {}


# ----------------------------------------------------------------- runner
class FakeClient:
    def __init__(self):
        self.calls: list[str] = []
        self.error = False

    def character_planets(self, cid, role):
        self.calls.append("planets")
        if self.error:
            raise ESIError("HTTP 500")
        return [{"planet_id": 11, "planet_type": "barren", "solar_system_id": 1, "upgrade_level": 5,
                 "last_update": NOW.isoformat()}]

    def character_planet(self, cid, planet_id, role):
        self.calls.append(f"planet{planet_id}")
        return _LAYOUT


def _setup(alerts=(logic.PI_EXTRACTOR_EXPIRY,)):
    role = f"esi:{ALICE}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=ALICE, character_name="Alice", access_token="a", refresh_token="r",
        expires_at=9999999999.0, scopes=SCOPE)))
    storage.set_alert_destination(UID)
    _share(ALICE, "planets", "char_alerts")
    for t in alerts:
        aa.do_set_subscription(ALICE, t, True, lead_hours=12)


def _snapshot():
    storage.replace_character_pi_colonies(ALICE, [_row(11)])
    storage.upsert_esi_freshness("character", ALICE, "planets", success=True)


def _projection(expiry_hours, live_expiry_hours=None):
    """`_pi_colonies` stub: snapshot rows project to `expiry_hours`, live rows
    (they carry a different last_update) to `live_expiry_hours`."""
    def fake(rows, now):
        live = rows and rows[0]["last_update"] == NOW.isoformat()
        h = live_expiry_hours if live and live_expiry_hours is not None else expiry_hours
        return [{"planet_id": rows[0]["planet_id"], "planet_name": "Planet V", "projection": {
            "last_update": rows[0]["last_update"], "full_at": (NOW + timedelta(hours=h)).isoformat(),
            "inputs_empty_at": None,
            "extractors": [{"pin_id": 3, "expiry_time": (NOW + timedelta(hours=h)).isoformat(),
                            "product_name": "Heavy Metals"}]}}] if rows else []
    return fake


def _run(client, now=NOW):
    return runner.run_for_tenant(now=now, client=client, poll_minutes=10)


def test_pi_subscription_adds_planets_demand_and_reset_state(tenant):
    _setup()
    subs = runner.enabled_subscriptions()
    assert runner.pi_demand(subs) == {("character", ALICE, "planets")}
    assert runner.pi_demand([(ALICE, logic.MAIL_NEW, False, 12)]) == set()
    aa.do_set_subscription(ALICE, "pi_pad_full", True)
    with pytest.raises(ActionError):
        aa.do_set_subscription(ALICE, "pi_inputs_empty", True, lead_hours=999)


def test_runner_requests_the_planets_demand(tenant, monkeypatch):
    _setup()
    seen = []
    monkeypatch.setattr(runner.esi_orchestrator, "do_sync_due", lambda **kw: seen.append(kw))
    _run(FakeClient())
    assert seen[0]["demand"] == {("character", ALICE, "planets")} and seen[0]["granted_tools"] == {"char_alerts"}


def test_expiry_alert_needs_snapshot_live_confirmation_and_is_sent_once(tenant, _env, monkeypatch):
    _setup()
    monkeypatch.setattr(runner, "_pi_colonies", _projection(5.0))
    fake = FakeClient()
    _run(fake)                                                   # never synced: nothing
    assert fake.calls == [] and _env == []
    _snapshot()
    _run(fake)
    assert fake.calls == ["planets", "planet11"] and len(_env) == 1
    assert "Alice" in _env[0] and "Planet V" in _env[0] and "expires in 5 h" in _env[0]
    assert storage.get_pi_alert_states(ALICE)[(11, logic.PI_EXTRACTOR_EXPIRY)][0].startswith("expiry:3:")
    _run(fake, now=NOW + timedelta(hours=1))
    assert len(_env) == 1 and fake.calls == ["planets", "planet11"]  # same key: no resend, no live call


def test_player_reset_since_the_snapshot_produces_no_false_alarm(tenant, _env, monkeypatch):
    _setup()
    _snapshot()
    monkeypatch.setattr(runner, "_pi_colonies", _projection(5.0, live_expiry_hours=60.0))
    _run(FakeClient())
    assert _env == [] and storage.get_pi_alert_states(ALICE) == {}


def test_failed_live_check_and_failed_delivery_back_off_without_a_key(tenant, _env, monkeypatch):
    _setup()
    _snapshot()
    monkeypatch.setattr(runner, "_pi_colonies", _projection(5.0))
    fake = FakeClient()
    fake.error = True
    _run(fake)
    assert _env == [] and fake.calls == ["planets"]
    fake.error = False
    _run(fake, now=NOW + timedelta(minutes=5))                   # backing off
    assert fake.calls == ["planets"]

    def boom(uid, text):
        raise discord_client.DiscordRateLimited(3)
    monkeypatch.setattr(discord_client, "send_dm", boom)
    _run(fake, now=NOW + timedelta(minutes=20))
    assert storage.get_pi_alert_states(ALICE) == {}              # no key: announced once Discord is back
    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: _env.append(text))
    _run(fake, now=NOW + timedelta(minutes=40))
    assert len(_env) == 1


def test_revoked_sharing_blocks_pi_alerts(tenant, _env, monkeypatch):
    _setup()
    _snapshot()
    monkeypatch.setattr(runner, "_pi_colonies", _projection(5.0))
    with storage.connect() as conn:
        conn.execute("DELETE FROM esi_sharing WHERE data_kind = 'planets' AND tool_key = 'char_alerts'")
    fake = FakeClient()
    _run(fake)
    assert _env == [] and fake.calls == []


def test_each_pi_type_has_its_own_state_per_planet(tenant, _env, monkeypatch):
    _setup(alerts=(logic.PI_EXTRACTOR_EXPIRY, logic.PI_PAD_FULL))
    _snapshot()
    monkeypatch.setattr(runner, "_pi_colonies", _projection(5.0))
    _run(FakeClient())
    assert len(_env) == 2
    assert set(storage.get_pi_alert_states(ALICE)) == {(11, logic.PI_EXTRACTOR_EXPIRY), (11, logic.PI_PAD_FULL)}

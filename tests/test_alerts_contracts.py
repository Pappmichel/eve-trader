"""Discord contract alert: which contracts count as finished/expired, the
message, and the job body (baseline, once per contract, failures, sharing)."""
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

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_character_management_schema, _apply_esi_access_schema,
    _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema, tenant,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_refining import _apply_refining_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

ALICE = 3001
UID = "123456789012345678"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
SCOPES = "esi-contracts.read_character_contracts.v1"
_TABLES = ("alert_state", "alert_subscriptions", "alert_destinations", "esi_sharing", "contract_alert_state",
           "tenant_tokens")


def _contract(contract_id, status="outstanding", expires_in_h=24.0, issuer=ALICE, **kw):
    return {"contract_id": contract_id, "issuer_id": issuer, "status": status, "type": "item_exchange",
            "title": "", "price": 0.0, "reward": 0.0,
            "date_expired": (NOW + timedelta(hours=expires_in_h)).isoformat(), **kw}


# ------------------------------------------------------------------ pure logic
def test_events_are_finished_or_past_expiry_and_issued_by_the_character():
    contracts = [
        _contract(1, "finished"), _contract(2, "outstanding", expires_in_h=-1), _contract(3, "outstanding"),
        _contract(4, "finished", issuer=999), _contract(5, "in_progress", expires_in_h=-5),
        _contract(6, "finished_issuer"), _contract(7, "deleted", expires_in_h=-1),
    ]

    events = logic.contract_events(contracts, ALICE, NOW)

    assert [(i, e) for i, e, _c in events] == [(1, "finished"), (2, "expired"), (6, "finished")]


def test_message_lists_type_title_value_items_and_place():
    events = [
        (10, "finished", _contract(10, "finished", title="Doctrine Ferox", price=125_500_000.0, start_location_id=60003760)),
        (11, "expired", _contract(11, price=2_500_000_000.0)),
        (12, "finished", _contract(12, "finished", type="courier", title="", reward=4_000_000.0)),
    ]

    text = logic.contract_message(events, "Alice", items={11: "Rifter x2, 1 more"}, places={60003760: "Jita IV - Moon 4"})

    assert text.splitlines() == [
        "Alice: 2 contracts finished, 1 contract expired.",
        '- Finished: Item exchange "Doctrine Ferox", 125.5M ISK at Jita IV - Moon 4',
        '- Expired: Item exchange "Rifter x2, 1 more", 2.50B ISK',
        "- Finished: Courier, 4.0M ISK",
    ]


def test_long_lists_are_cut():
    events = [(i, "finished", _contract(i, "finished")) for i in range(logic.MAX_CONTRACTS_LISTED + 3)]
    assert logic.contract_message(events, "Alice").splitlines()[-1] == "... and 3 more"


# ------------------------------------------------------------------ job body
class FakeClient:
    def __init__(self, contracts=None):
        self.contracts = contracts or []
        self.error = False
        self.calls: list[str] = []

    def character_contracts(self, cid, auth_role):
        self.calls.append("contracts")
        if self.error:
            raise ESIError("HTTP 500")
        return self.contracts

    def character_contract_items(self, cid, contract_id, auth_role):
        self.calls.append(f"items{contract_id}")
        return [{"type_id": 587, "quantity": 2, "is_included": True}]


@pytest.fixture
def env(tenant, monkeypatch):  # noqa: F811
    pg_helpers.wipe_tables(*_TABLES)
    sent: list[str] = []
    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: sent.append(text))
    monkeypatch.setattr(runner.esi_orchestrator, "do_sync_due", lambda **kw: None)
    monkeypatch.setattr(storage, "sde_type_names", lambda ids: {587: "Rifter"})
    role = f"esi:{ALICE}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=ALICE, character_name="Alice", access_token="a", refresh_token="r",
        expires_at=9999999999.0, scopes=SCOPES)))
    storage.set_alert_destination(UID)
    with storage.connect() as conn:
        conn.execute("INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                     "VALUES ('character', ?, 'contracts', 'char_alerts')", (ALICE,))
    yield sent
    pg_helpers.wipe_tables(*_TABLES)


def _run(client, now=NOW):
    return runner.run_for_tenant(now=now, client=client, poll_minutes=10)


@pg_helpers.postgres_required()
def test_first_poll_is_a_baseline_then_each_contract_is_announced_once(env):
    aa.do_set_subscription(ALICE, "contract_status", True)
    fake = FakeClient([_contract(1, "finished", title="Old sale")])

    _run(fake)                                               # baseline: the old sale is not announced
    assert env == []
    fake.contracts += [_contract(2, "finished", price=5_000_000.0), _contract(3, "outstanding", expires_in_h=1)]
    _run(fake, NOW + timedelta(minutes=20))
    assert len(env) == 1 and env[0].startswith("Alice: 1 contract finished.")
    assert '"Rifter x2"' in env[0] and fake.calls.count("items2") == 1

    _run(fake, NOW + timedelta(minutes=25))                  # inside the poll interval: no ESI call
    assert fake.calls.count("contracts") == 2
    _run(fake, NOW + timedelta(hours=2))                     # contract 3 is now past its expiry
    assert len(env) == 2 and env[1].startswith("Alice: 1 contract expired.")
    _run(fake, NOW + timedelta(hours=3))
    assert len(env) == 2


@pg_helpers.postgres_required()
def test_a_failed_send_or_poll_keeps_the_contract_for_later(env, monkeypatch):
    aa.do_set_subscription(ALICE, "contract_status", True)
    fake = FakeClient()
    _run(fake)
    fake.contracts = [_contract(2, "finished")]

    def broken(uid, text):
        raise discord_client.DiscordError("Discord HTTP 500")
    monkeypatch.setattr(discord_client, "send_dm", broken)
    _run(fake, NOW + timedelta(minutes=20))
    assert storage.seen_contract_alerts(ALICE) == set()

    monkeypatch.setattr(discord_client, "send_dm", lambda uid, text: env.append(text))
    fake.error = True
    _run(fake, NOW + timedelta(minutes=40))
    fake.error = False
    _run(fake, NOW + timedelta(minutes=60))
    assert len(env) == 1 and storage.seen_contract_alerts(ALICE) == {(2, "finished")}


@pg_helpers.postgres_required()
def test_opt_in_needs_contracts_shared_with_alerts(env):
    with storage.connect() as conn:
        conn.execute("DELETE FROM esi_sharing")
    with pytest.raises(ActionError, match="Share contracts"):
        aa.do_set_subscription(ALICE, "contract_status", True)

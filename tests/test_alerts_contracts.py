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


def test_message_names_the_buyer_and_sold_out_fittings():
    events = [
        (10, "finished", _contract(10, "finished", title="GSF - Typhoon", price=295_000_000.0, acceptor_id=91)),
        (11, "finished", _contract(11, "finished", type="courier", reward=4_000_000.0, acceptor_id=92)),
        (12, "expired", _contract(12, acceptor_id=91)),
    ]

    text = logic.contract_message(events, "Alice", acceptors={91: "Bob", 92: "Hauler Corp"},
                                  sold_out=["Typhoon DPS"])

    assert text.splitlines()[1:] == [
        '- Finished: Item exchange "GSF - Typhoon", 295.0M ISK, bought by Bob',
        "- Finished: Courier, 4.0M ISK, delivered by Hauler Corp",
        "- Expired: Item exchange",
        '- Sold out: fitting "Typhoon DPS" has no contract left',
    ]


def _doctrine_row(contract_id, fitting_id, status="outstanding", validation="valid", expires_in_h=24.0):
    return (contract_id, "esi:1", False, ALICE, 1, status, "", 1.0, NOW + timedelta(hours=expires_in_h),
            fitting_id, 1.0, validation, NOW)


def test_sold_out_only_when_a_sale_empties_a_fit(monkeypatch):
    from types import SimpleNamespace
    from eve_trader.doctrine import engine

    def cand(fid, name, hull):
        return SimpleNamespace(fitting=SimpleNamespace(fitting_id=fid, name=name, hull_type_id=hull),
                               exact_soll={1: 1.0}, consume_soll={})
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [
        cand("f1", "Typhoon DPS", 100), cand("f1b", "Typhoon DPS (other doctrine)", 100),
        cand("f2", "Ferox", 200), cand("f3", "Scimitar", 300), cand("f4", "Empty", 400)])
    monkeypatch.setattr(engine.storage, "list_doctrine_contracts", lambda: [
        _doctrine_row(1, "f1"),                          # sold, and its pooled twin is gone too
        _doctrine_row(2, "f1b", expires_in_h=-1),        # past expiry: not available
        _doctrine_row(3, "f2"), _doctrine_row(4, "f2"),  # one Ferox left
        _doctrine_row(5, "f3"), _doctrine_row(6, "f3"),  # the other one finished in the live list
        _doctrine_row(7, "f4", validation="invalid"),    # never counted: not "previously available"
    ])

    sold = {1, 3, 5, 7}
    assert engine.sold_out_fittings(sold, sold | {6}, NOW) == ["Typhoon DPS", "Scimitar"]


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

    def resolve_names_cached(self, ids):
        return {i: f"Buyer {i}" for i in ids}

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
def test_a_sale_names_the_buyer_and_reports_sold_out_fittings_with_the_doctrine_grant(env, monkeypatch):
    from eve_trader.doctrine import engine
    calls = []
    monkeypatch.setattr(engine, "sold_out_by_contract", lambda sold, unavailable, now: calls.append(
        (sold, unavailable)) or {2: "Typhoon DPS"})
    monkeypatch.setattr(runner.tenant_eligibility, "granted_tools", lambda tid: {"char_alerts", "doctrine"})
    aa.do_set_subscription(ALICE, "contract_status", True)
    fake = FakeClient([_contract(9, "outstanding", expires_in_h=-1)])
    _run(fake)
    fake.contracts += [_contract(2, "finished", title="GSF - Typhoon", acceptor_id=91), _contract(3, "deleted")]

    _run(fake, NOW + timedelta(minutes=20))

    assert calls == [({2}, {2, 3, 9})]
    assert "bought by Buyer 91" in env[0]
    assert env[0].splitlines()[-1] == '- Sold out: fitting "Typhoon DPS" has no contract left'

    monkeypatch.setattr(runner.tenant_eligibility, "granted_tools", lambda tid: {"char_alerts"})
    fake.contracts.append(_contract(4, "finished", acceptor_id=91))
    _run(fake, NOW + timedelta(minutes=40))
    assert len(calls) == 1 and "Sold out" not in env[1]


@pg_helpers.postgres_required()
def test_opt_in_needs_contracts_shared_with_alerts(env):
    with storage.connect() as conn:
        conn.execute("DELETE FROM esi_sharing")
    with pytest.raises(ActionError, match="Share contracts"):
        aa.do_set_subscription(ALICE, "contract_status", True)


# ------------------------------------------------------------ filters (GH #382)
def _kept_ids(events, f, matches=None, sold_out=None):
    return [i for i, _e, _c in logic.filter_contract_events(events, f, matches, sold_out)[0]]


def test_filters_split_events_by_type_doctrine_match_and_sell_out():
    events = [(1, "finished", _contract(1, "finished")), (2, "expired", _contract(2)),
              (3, "finished", _contract(3, "finished", type="courier")), (4, "finished", _contract(4, "finished"))]

    assert _kept_ids(events, logic.ContractFilters()) == [1, 2, 3, 4]
    assert _kept_ids(events, logic.ContractFilters(notify_expired=False)) == [1, 3, 4]
    assert _kept_ids(events, logic.ContractFilters(notify_finished=False)) == [2]
    assert _kept_ids(events, logic.ContractFilters(doctrine_only=True), {1: "Ferox", 2: "Ferox"}) == [1, 2]
    assert _kept_ids(events, logic.ContractFilters(doctrine_only=True), None) == []   # no doctrine grant
    assert _kept_ids(events, logic.ContractFilters(sold_out_only=True), None, {1: "Ferox", 3: "X"}) == [1]
    kept, dropped = logic.filter_contract_events(events, logic.ContractFilters(sold_out_only=True), None, {})
    assert kept == [] and len(dropped) == 4


def test_filter_validation_and_interval():
    assert logic.contract_filter_error(logic.ContractFilters()) is None
    assert logic.contract_filter_error(logic.ContractFilters(min_interval_minutes=5))
    assert logic.contract_filter_error(logic.ContractFilters(notify_finished=False, notify_expired=False))
    assert logic.contract_filter_error(logic.ContractFilters(notify_finished=False, sold_out_only=True))
    f = logic.ContractFilters(min_interval_minutes=60)
    assert logic.contract_interval_open(f, None, NOW)
    assert not logic.contract_interval_open(f, NOW - timedelta(minutes=59), NOW)
    assert logic.contract_interval_open(f, NOW - timedelta(minutes=60), NOW)
    assert logic.contract_interval_open(logic.ContractFilters(), NOW, NOW)


def test_contract_doctrine_matches_only_valid_fits(monkeypatch):
    from types import SimpleNamespace
    from eve_trader.doctrine import engine
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [
        SimpleNamespace(fitting=SimpleNamespace(fitting_id="f1", name="Ferox", hull_type_id=1),
                        exact_soll={}, consume_soll={})])
    monkeypatch.setattr(engine.storage, "list_doctrine_contracts", lambda: [
        _doctrine_row(1, "f1"), _doctrine_row(2, "f1", validation="tolerable"),
        _doctrine_row(3, "f1", validation="invalid"), _doctrine_row(4, "gone"), _doctrine_row(5, "f1")])

    assert engine.contract_doctrine_matches({1, 2, 3, 4}) == {1: "Ferox", 2: "Ferox"}


@pg_helpers.postgres_required()
def test_finished_only_filter_marks_expired_seen_without_a_dm(env):
    aa.do_set_subscription(ALICE, "contract_status", True,
                           contract_filters={"notify_finished": True, "notify_expired": False})
    fake = FakeClient()
    _run(fake)
    fake.contracts = [_contract(2, "outstanding", expires_in_h=-1)]

    _run(fake, NOW + timedelta(minutes=20))

    assert env == []
    assert storage.seen_contract_alerts(ALICE) == {(2, "expired")}


@pg_helpers.postgres_required()
def test_min_interval_holds_events_and_sends_them_together(env):
    aa.do_set_subscription(ALICE, "contract_status", True, contract_filters={"min_interval_minutes": 60})
    fake = FakeClient()
    _run(fake)
    fake.contracts = [_contract(2, "finished")]
    _run(fake, NOW + timedelta(minutes=20))
    assert len(env) == 1

    fake.contracts.append(_contract(3, "finished"))
    _run(fake, NOW + timedelta(minutes=40))                  # 20 min after the last DM: held back
    fake.contracts.append(_contract(4, "finished"))
    _run(fake, NOW + timedelta(minutes=60))
    assert len(env) == 1 and storage.seen_contract_alerts(ALICE) == {(2, "finished")}

    _run(fake, NOW + timedelta(minutes=80))                  # 60 min passed: one DM with both
    assert len(env) == 2 and env[1].startswith("Alice: 2 contracts finished.")


def _grant_doctrine(monkeypatch):
    def grants(tid):
        return {"char_alerts", "doctrine"}
    monkeypatch.setattr(runner.tenant_eligibility, "granted_tools", grants)
    monkeypatch.setattr(aa.tenant_eligibility, "granted_tools", grants)


@pg_helpers.postgres_required()
def test_sold_out_only_sends_just_the_emptying_sale(env, monkeypatch):
    from eve_trader.doctrine import engine
    monkeypatch.setattr(engine, "sold_out_by_contract", lambda sold, unavailable, now: {3: "Ferox"})
    _grant_doctrine(monkeypatch)
    aa.do_set_subscription(ALICE, "contract_status", True, contract_filters={"sold_out_only": True})
    fake = FakeClient()
    _run(fake)
    fake.contracts = [_contract(2, "finished", title="Rifter"), _contract(3, "finished", title="Ferox")]

    _run(fake, NOW + timedelta(minutes=20))

    assert len(env) == 1
    assert '"Ferox"' in env[0] and '"Rifter"' not in env[0]
    assert env[0].splitlines()[-1] == '- Sold out: fitting "Ferox" has no contract left'
    assert storage.seen_contract_alerts(ALICE) == {(2, "finished"), (3, "finished")}


@pg_helpers.postgres_required()
def test_doctrine_only_keeps_events_while_the_lookup_fails(env, monkeypatch):
    from eve_trader.doctrine import engine
    _grant_doctrine(monkeypatch)
    aa.do_set_subscription(ALICE, "contract_status", True, contract_filters={"doctrine_only": True})
    fake = FakeClient()
    _run(fake)
    fake.contracts = [_contract(2, "finished"), _contract(3, "finished")]

    def broken(ids):
        raise RuntimeError("db down")
    monkeypatch.setattr(engine, "contract_doctrine_matches", broken)
    monkeypatch.setattr(engine, "sold_out_by_contract", lambda *a: {})
    _run(fake, NOW + timedelta(minutes=20))
    assert env == [] and storage.seen_contract_alerts(ALICE) == set()

    monkeypatch.setattr(engine, "contract_doctrine_matches", lambda ids: {3: "Ferox"})
    _run(fake, NOW + timedelta(minutes=40))
    assert len(env) == 1 and env[0].startswith("Alice: 1 contract finished.")
    assert storage.seen_contract_alerts(ALICE) == {(2, "finished"), (3, "finished")}


@pg_helpers.postgres_required()
def test_filter_settings_round_trip_and_validation(env, monkeypatch):
    monkeypatch.setattr(aa.tenant_eligibility, "granted_tools", lambda tid: {"char_alerts"})
    with pytest.raises(ActionError, match="Doctrine"):
        aa.do_set_subscription(ALICE, "contract_status", True, contract_filters={"doctrine_only": True})
    with pytest.raises(ActionError, match="interval"):
        aa.do_set_subscription(ALICE, "contract_status", True, contract_filters={"min_interval_minutes": 5})
    with pytest.raises(ActionError, match="Only contract alerts"):
        aa.do_set_subscription(ALICE, "mail_new", False, contract_filters={})

    out = aa.do_set_subscription(ALICE, "contract_status", True,
                                 contract_filters={"min_interval_minutes": 60, "notify_expired": False})
    assert out["contract_filters"]["min_interval_minutes"] == 60
    aa.do_set_subscription(ALICE, "contract_status", False)          # None keeps the stored filters
    settings = aa.do_get_alert_settings()
    alice = next(c for c in settings["characters"] if c["character_id"] == ALICE)
    assert alice["alerts"]["contract_status"]["contract_filters"] == {
        "min_interval_minutes": 60, "notify_finished": True, "notify_expired": False,
        "doctrine_only": False, "sold_out_only": False}
    assert settings["doctrine_granted"] is False

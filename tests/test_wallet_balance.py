"""wallet_balance ESI data kind (PORTFOLIO_REWORK_PLAN.md section 3):
esi_client.corporation_wallet_balances, the two fetchers, and the
character_wallet_balances/corp_wallet_balances storage functions."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.config import WALLET_DIVISION_IDS
from eve_trader.esi_client import ESIError
from eve_trader.esi_data.fetchers import (
    fetch_character_wallet_balance, fetch_corporation_wallet_balance,
)

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant, tenant_pair  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

CHAR_ID = 1001
CORP_ID = 98000001
ALICE_ROLE = "producer:1001"
BOB_ROLE = "producer:1002"


class CharWalletClient:
    def __init__(self, balance: float):
        self.balance = balance

    def character_wallet_balance(self, character_id, auth_role):
        return self.balance


class CorpWalletClient:
    """Per-role readable division sets, mirroring CorpWalletClient in
    test_esi_corp_wallet_coverage.py."""

    def __init__(self, readable: dict[str, dict[int, float]]):
        self.readable = readable
        self.calls: list[str] = []

    def corporation_wallet_balances(self, corporation_id, auth_role):
        self.calls.append(auth_role)
        if auth_role not in self.readable:
            raise ESIError(f"403 for {auth_role}")
        return [{"division": d, "balance": b} for d, b in self.readable[auth_role].items()]


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("character_wallet_balances", "corp_wallet_balances")
    yield


def test_fetch_character_wallet_balance_stores_and_returns(tenant):
    result = fetch_character_wallet_balance(CharWalletClient(1234.5), CHAR_ID, ALICE_ROLE, "Alice")
    assert result == {"written": 1, "balance": 1234.5}
    assert storage.load_character_wallet_balance(CHAR_ID) == 1234.5


def test_fetch_character_wallet_balance_upsert_overwrites(tenant):
    fetch_character_wallet_balance(CharWalletClient(100.0), CHAR_ID, ALICE_ROLE, "Alice")
    fetch_character_wallet_balance(CharWalletClient(200.0), CHAR_ID, ALICE_ROLE, "Alice")
    assert storage.load_character_wallet_balance(CHAR_ID) == 200.0


def test_load_character_wallet_balance_none_when_never_synced(tenant):
    assert storage.load_character_wallet_balance(CHAR_ID) is None


def test_fetch_corporation_wallet_balance_unions_divisions_across_roles(tenant):
    client = CorpWalletClient({
        ALICE_ROLE: {1: 10.0},
        BOB_ROLE: {d: float(d) for d in WALLET_DIVISION_IDS},
    })
    result = fetch_corporation_wallet_balance(
        client, CORP_ID, ALICE_ROLE, "Test Corp (corp)",
        candidate_auth_roles=[ALICE_ROLE, BOB_ROLE],
    )
    assert sorted(result["divisions"]) == list(WALLET_DIVISION_IDS)
    stored = storage.load_corp_wallet_balances(CORP_ID)
    # Every candidate role is called for its own full division set (this
    # endpoint returns every division in one call, unlike the per-division
    # transactions/journal endpoints) - a later role's figure wins on any
    # division more than one role could read, since the balance figure is
    # the same real ESI value either way.
    assert stored[1] == 1.0  # only Bob's response carries division 1 here
    assert stored[2] == 2.0


def test_fetch_corporation_wallet_balance_partial_read_does_not_delete_unread(tenant):
    storage.replace_corp_wallet_balances(CORP_ID, {1: 1.0, 2: 2.0, 3: 3.0})
    client = CorpWalletClient({ALICE_ROLE: {1: 999.0}})

    fetch_corporation_wallet_balance(client, CORP_ID, ALICE_ROLE, "Test Corp (corp)")

    stored = storage.load_corp_wallet_balances(CORP_ID)
    assert stored[1] == 999.0
    assert stored[2] == 2.0  # untouched
    assert stored[3] == 3.0  # untouched


def test_fetch_corporation_wallet_balance_raises_when_no_role_can_read(tenant):
    client = CorpWalletClient({})
    with pytest.raises(ESIError):
        fetch_corporation_wallet_balance(client, CORP_ID, ALICE_ROLE, "Test Corp (corp)")
    assert storage.load_corp_wallet_balances(CORP_ID) == {}


def test_replace_corp_wallet_balances_empty_is_noop(tenant):
    storage.replace_corp_wallet_balances(CORP_ID, {1: 1.0})
    storage.replace_corp_wallet_balances(CORP_ID, {})
    assert storage.load_corp_wallet_balances(CORP_ID) == {1: 1.0}


def test_wallet_balances_isolated_between_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.upsert_character_wallet_balance(CHAR_ID, 500.0)
        storage.replace_corp_wallet_balances(CORP_ID, {1: 50.0})
    with storage.tenant_context(tenant_b):
        assert storage.load_character_wallet_balance(CHAR_ID) is None
        assert storage.load_corp_wallet_balances(CORP_ID) == {}

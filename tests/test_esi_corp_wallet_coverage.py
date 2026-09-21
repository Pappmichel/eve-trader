"""Corp wallet coverage: union readable divisions across members, and
never DELETE unread ones (or anything, on a total failure).

Live installs currently have no corporation wallet/trading sharing row
(the conservative backfill left buyer/seller corp_kinds empty), so these
tests are the only proof until that sharing is switched on.
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from eve_trader import storage
from eve_trader.auth import TokenRecord
from eve_trader.config import WALLET_DIVISION_IDS
from eve_trader.esi_client import ESIError
from eve_trader.esi_data.fetchers import fetch_corporation_wallet
from eve_trader.esi_data.orchestrator import do_sync_for_tool

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
BOB = 1002
CORP = 98000001
TYPE_ID = 34
LOCATION_ID = 1000000000001
DATE = "2026-01-01T00:00:00+00:00"
CORP_WALLET_SCOPE = "esi-wallet.read_corporation_wallets.v1"
ALICE_ROLE = f"producer:{ALICE}"
BOB_ROLE = f"producer:{BOB}"


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(
        "esi_wallet_transactions", "esi_wallet_journal",
        "esi_sharing", "esi_freshness", "tenant_tokens",
    )
    yield


def _share(owner_type: str, owner_id: int, data_kind: str, tool_key: str) -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            (owner_type, owner_id, data_kind, tool_key),
        )


def _tokens(*chars: tuple[int, str], scopes: str = CORP_WALLET_SCOPE) -> None:
    for cid, name in chars:
        role = f"producer:{cid}"
        storage.save_tenant_token(role, asdict(TokenRecord(
            role=role, character_id=cid, character_name=name,
            access_token="a", refresh_token="r", expires_at=9999999999.0,
            scopes=scopes,
        )))


def _corp_txn(division: int, txn_id: int) -> tuple:
    return (division, txn_id, DATE, TYPE_ID, LOCATION_ID, 1.0, 1, False, txn_id)


def _corp_journal(division: int, jid: int) -> tuple:
    return (division, jid, DATE, "market_transaction", 1.0)


def _seed_all_divisions(txn_base: int = 2000) -> None:
    storage.replace_wallet_transactions(
        [_corp_txn(d, txn_base + d) for d in WALLET_DIVISION_IDS],
        owner_type="corporation", owner_id=CORP, divisions=list(WALLET_DIVISION_IDS),
    )
    storage.replace_wallet_journal(
        [_corp_journal(d, txn_base + d) for d in WALLET_DIVISION_IDS],
        owner_type="corporation", owner_id=CORP, divisions=list(WALLET_DIVISION_IDS),
    )


def _txn_ids() -> dict[int, int]:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT division, transaction_id FROM esi_wallet_transactions "
            "WHERE owner_corporation_id = ? ORDER BY division",
            (CORP,),
        ).fetchall()
    return {int(d): int(tid) for d, tid in rows}


def _journal_ids() -> dict[int, int]:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT division, journal_id FROM esi_wallet_journal "
            "WHERE owner_corporation_id = ? ORDER BY division",
            (CORP,),
        ).fetchall()
    return {int(d): int(jid) for d, jid in rows}


class CorpWalletClient:
    """Per-role readable division sets. 403 on anything else."""

    def __init__(self, readable: dict[str, set[int]]):
        self.readable = readable
        self.txn_calls: list[tuple[str, int]] = []
        self.journal_calls: list[tuple[str, int]] = []

    def character_public_info(self, character_id):
        return {"corporation_id": CORP}

    def corporation_public_info(self, corporation_id):
        return {"name": "Test Corp"}

    def corporation_wallet_transactions(self, corporation_id, division, auth_role, from_id=None):
        self.txn_calls.append((auth_role, division))
        if division not in self.readable.get(auth_role, set()):
            raise ESIError(f"403 wallet division {division}")
        return [{
            "transaction_id": 100 + division,
            "date": "2026-01-01T00:00:00Z",
            "type_id": TYPE_ID,
            "location_id": LOCATION_ID,
            "unit_price": 1.0,
            "quantity": 1,
            "is_buy": False,
            "journal_ref_id": 100 + division,
        }]

    def corporation_wallet_journal(self, corporation_id, division, auth_role):
        self.journal_calls.append((auth_role, division))
        if division not in self.readable.get(auth_role, set()):
            raise ESIError(f"403 wallet journal {division}")
        return [{
            "id": 100 + division,
            "date": "2026-01-01T00:00:00Z",
            "ref_type": "market_transaction",
            "amount": 1.0,
        }]


def test_fetcher_unions_divisions_across_candidate_roles(tenant):
    """Member A reads only division 1, member B reads all -> result covers all."""
    client = CorpWalletClient({
        ALICE_ROLE: {1},
        BOB_ROLE: set(WALLET_DIVISION_IDS),
    })
    result = fetch_corporation_wallet(
        client, CORP, ALICE_ROLE, "Test Corp (corp)",
        candidate_auth_roles=[ALICE_ROLE, BOB_ROLE],
    )
    assert sorted(result["divisions"]) == list(WALLET_DIVISION_IDS)
    assert _txn_ids() == {d: 100 + d for d in WALLET_DIVISION_IDS}
    assert _journal_ids() == {d: 100 + d for d in WALLET_DIVISION_IDS}
    # Alice is tried first (all 7); Bob only for the unread remainder.
    assert (ALICE_ROLE, 1) in client.txn_calls
    assert (BOB_ROLE, 2) in client.txn_calls
    assert (BOB_ROLE, 1) not in client.txn_calls


def test_fetcher_partial_read_does_not_delete_unread_divisions(tenant):
    """A run that can only read division 1 must NOT delete existing 2-7."""
    _seed_all_divisions()
    client = CorpWalletClient({ALICE_ROLE: {1}})
    result = fetch_corporation_wallet(client, CORP, ALICE_ROLE, "Test Corp (corp)")
    assert result["divisions"] == [1]
    txns = _txn_ids()
    journals = _journal_ids()
    assert txns[1] == 101
    assert journals[1] == 101
    for d in range(2, 8):
        assert txns[d] == 2000 + d
        assert journals[d] == 2000 + d


def test_fetcher_zero_readable_divisions_skips_delete(tenant):
    """Zero readable divisions is a failed fetch; existing rows stay
    (decision 6)."""
    _seed_all_divisions()
    client = CorpWalletClient({ALICE_ROLE: set()})
    with pytest.raises(ESIError):
        fetch_corporation_wallet(client, CORP, ALICE_ROLE, "Test Corp (corp)")
    assert _txn_ids() == {d: 2000 + d for d in WALLET_DIVISION_IDS}
    assert _journal_ids() == {d: 2000 + d for d in WALLET_DIVISION_IDS}


def test_orchestrator_tries_later_member_for_unread_wallet_divisions(tenant):
    """Junior Accountant listed first used to `break` on partial success
    and never try the full Accountant. The orchestrator now passes every
    candidate role into the wallet fetcher."""
    _share("corporation", CORP, "wallet", "trading")
    _tokens((ALICE, "Alice"), (BOB, "Bob"))
    client = CorpWalletClient({
        ALICE_ROLE: {1},
        BOB_ROLE: set(WALLET_DIVISION_IDS),
    })
    result = do_sync_for_tool("trading", client=client)
    assert result["ok"] is True
    corp = next(r for r in result["owners"] if r["owner_id"] == CORP)
    assert sorted(corp["kinds"]["wallet"]["divisions"]) == list(WALLET_DIVISION_IDS)
    assert _txn_ids() == {d: 100 + d for d in WALLET_DIVISION_IDS}
    assert (ALICE_ROLE, 1) in client.txn_calls
    assert (BOB_ROLE, 7) in client.txn_calls


def test_orchestrator_partial_corp_wallet_keeps_unread_divisions(tenant):
    _share("corporation", CORP, "wallet", "trading")
    _tokens((ALICE, "Alice"))
    _seed_all_divisions()
    client = CorpWalletClient({ALICE_ROLE: {1}})
    result = do_sync_for_tool("trading", client=client)
    assert result["ok"] is True
    txns = _txn_ids()
    assert txns[1] == 101
    for d in range(2, 8):
        assert txns[d] == 2000 + d


def test_orchestrator_zero_readable_corp_wallet_is_a_failed_fetch(tenant):
    _share("corporation", CORP, "wallet", "trading")
    _tokens((ALICE, "Alice"))
    _seed_all_divisions()
    client = CorpWalletClient({ALICE_ROLE: set()})
    result = do_sync_for_tool("trading", client=client)
    assert result["ok"] is False
    assert _txn_ids() == {d: 2000 + d for d in WALLET_DIVISION_IDS}
    assert _journal_ids() == {d: 2000 + d for d in WALLET_DIVISION_IDS}
    with storage.connect() as conn:
        err = conn.execute(
            "SELECT last_error FROM esi_freshness "
            "WHERE owner_type = 'corporation' AND owner_id = ? AND data_kind = 'wallet'",
            (CORP,),
        ).fetchone()
    assert err is not None and err[0]

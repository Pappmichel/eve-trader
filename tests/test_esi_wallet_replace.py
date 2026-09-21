"""Corp wallet replace must not wipe unread divisions.

Character owners still replace the whole (division-0) partition.
"""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
CORP = 98000001
TYPE_ID = 34
LOCATION_ID = 1000000000001
DATE = "2026-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("esi_wallet_transactions", "esi_wallet_journal")
    yield


def _char_txn(txn_id: int) -> tuple:
    return (0, txn_id, DATE, TYPE_ID, LOCATION_ID, 1.0, 1, False, txn_id)


def _char_journal(jid: int) -> tuple:
    return (0, jid, DATE, "market_transaction", 1.0)


def _corp_txn(division: int, txn_id: int) -> tuple:
    return (division, txn_id, DATE, TYPE_ID, LOCATION_ID, 1.0, 1, False, txn_id)


def _corp_journal(division: int, jid: int) -> tuple:
    return (division, jid, DATE, "market_transaction", 1.0)


def _txn_ids(owner_type: str, owner_id: int) -> dict[int, int]:
    column = "owner_character_id" if owner_type == "character" else "owner_corporation_id"
    with storage.connect() as conn:
        rows = conn.execute(
            f"SELECT division, transaction_id FROM esi_wallet_transactions "
            f"WHERE {column} = ? ORDER BY division, transaction_id",
            (owner_id,),
        ).fetchall()
    return {int(d): int(tid) for d, tid in rows}


def _journal_ids(owner_type: str, owner_id: int) -> dict[int, int]:
    column = "owner_character_id" if owner_type == "character" else "owner_corporation_id"
    with storage.connect() as conn:
        rows = conn.execute(
            f"SELECT division, journal_id FROM esi_wallet_journal "
            f"WHERE {column} = ? ORDER BY division, journal_id",
            (owner_id,),
        ).fetchall()
    return {int(d): int(jid) for d, jid in rows}


def test_character_wallet_replace_still_wipes_the_whole_partition(tenant):
    storage.replace_wallet_transactions(
        [_char_txn(1), _char_txn(2)], owner_type="character", owner_id=ALICE,
    )
    storage.replace_wallet_journal(
        [_char_journal(1), _char_journal(2)], owner_type="character", owner_id=ALICE,
    )
    storage.replace_wallet_transactions(
        [_char_txn(9)], owner_type="character", owner_id=ALICE,
    )
    storage.replace_wallet_journal(
        [_char_journal(9)], owner_type="character", owner_id=ALICE,
    )
    assert _txn_ids("character", ALICE) == {0: 9}
    assert _journal_ids("character", ALICE) == {0: 9}


def test_character_wallet_empty_replace_still_deletes(tenant):
    storage.replace_wallet_transactions(
        [_char_txn(1)], owner_type="character", owner_id=ALICE,
    )
    storage.replace_wallet_journal(
        [_char_journal(1)], owner_type="character", owner_id=ALICE,
    )
    storage.replace_wallet_transactions([], owner_type="character", owner_id=ALICE)
    storage.replace_wallet_journal([], owner_type="character", owner_id=ALICE)
    assert _txn_ids("character", ALICE) == {}
    assert _journal_ids("character", ALICE) == {}


def test_corp_wallet_replace_only_deletes_written_divisions(tenant):
    storage.replace_wallet_transactions(
        [_corp_txn(d, 2000 + d) for d in range(1, 8)],
        owner_type="corporation", owner_id=CORP, divisions=list(range(1, 8)),
    )
    storage.replace_wallet_journal(
        [_corp_journal(d, 2000 + d) for d in range(1, 8)],
        owner_type="corporation", owner_id=CORP, divisions=list(range(1, 8)),
    )
    storage.replace_wallet_transactions(
        [_corp_txn(1, 101)],
        owner_type="corporation", owner_id=CORP, divisions=[1],
    )
    storage.replace_wallet_journal(
        [_corp_journal(1, 101)],
        owner_type="corporation", owner_id=CORP, divisions=[1],
    )
    txns = _txn_ids("corporation", CORP)
    journals = _journal_ids("corporation", CORP)
    assert txns[1] == 101
    assert journals[1] == 101
    for d in range(2, 8):
        assert txns[d] == 2000 + d
        assert journals[d] == 2000 + d


def test_corp_wallet_replace_infers_divisions_from_rows(tenant):
    storage.replace_wallet_transactions(
        [_corp_txn(d, 2000 + d) for d in range(1, 8)],
        owner_type="corporation", owner_id=CORP, divisions=list(range(1, 8)),
    )
    storage.replace_wallet_transactions(
        [_corp_txn(1, 101)],
        owner_type="corporation", owner_id=CORP,
    )
    txns = _txn_ids("corporation", CORP)
    assert txns[1] == 101
    for d in range(2, 8):
        assert txns[d] == 2000 + d


def test_corp_wallet_empty_replace_does_not_wipe(tenant):
    storage.replace_wallet_transactions(
        [_corp_txn(d, 2000 + d) for d in range(1, 8)],
        owner_type="corporation", owner_id=CORP, divisions=list(range(1, 8)),
    )
    storage.replace_wallet_journal(
        [_corp_journal(d, 2000 + d) for d in range(1, 8)],
        owner_type="corporation", owner_id=CORP, divisions=list(range(1, 8)),
    )
    storage.replace_wallet_transactions([], owner_type="corporation", owner_id=CORP)
    storage.replace_wallet_journal([], owner_type="corporation", owner_id=CORP)
    assert _txn_ids("corporation", CORP) == {d: 2000 + d for d in range(1, 8)}
    assert _journal_ids("corporation", CORP) == {d: 2000 + d for d in range(1, 8)}


def test_corp_wallet_empty_readable_division_clears_that_division_only(tenant):
    """A division that is readable but currently empty must still replace
    that division's previous rows, without touching the others."""
    storage.replace_wallet_transactions(
        [_corp_txn(d, 2000 + d) for d in range(1, 8)],
        owner_type="corporation", owner_id=CORP, divisions=list(range(1, 8)),
    )
    storage.replace_wallet_transactions(
        [], owner_type="corporation", owner_id=CORP, divisions=[1],
    )
    txns = _txn_ids("corporation", CORP)
    assert 1 not in txns
    for d in range(2, 8):
        assert txns[d] == 2000 + d

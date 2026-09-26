"""_page_wallet_transactions (eve_trader/esi_data/fetchers.py) is a pure
pagination helper - no Postgres needed, unlike most of this module's other
tests. Mirrors tests/test_wallet_transaction_pagination.py's own dedup
regression for trade_reconciliation.py's live-fetch equivalents - same
from_id-cursor pattern, same live production symptom (duplicate-key errors
on esi_wallet_transactions_pkey), same fix (T1-01 follow-up, independent
challenge pass, 2026-09-26)."""
from eve_trader.esi_data.fetchers import WALLET_TRANSACTIONS_PAGE_SIZE, _page_wallet_transactions


def _txn(transaction_id: int) -> dict:
    return {"transaction_id": transaction_id}


def test_pages_past_first_batch():
    page1 = [_txn(i) for i in range(WALLET_TRANSACTIONS_PAGE_SIZE)]
    page2 = [_txn(WALLET_TRANSACTIONS_PAGE_SIZE + i) for i in range(50)]
    pages = [page1, page2]

    def fetch_page(from_id):
        return pages.pop(0) if pages else []

    result = _page_wallet_transactions(fetch_page)

    assert len(result) == WALLET_TRANSACTIONS_PAGE_SIZE + 50


def test_boundary_transaction_repeated_across_pages_is_not_double_counted():
    """replace_wallet_transactions does a full delete-then-insert per sync
    (storage.py), so the only way to get a duplicate-key error on
    esi_wallet_transactions_pkey is the same transaction_id appearing twice
    within one fetch - confirmed live in production logs for a real
    character. from_id's oldest-of-previous-page value reappearing as the
    newest entry of the next page reproduces exactly that shape."""
    boundary_id = WALLET_TRANSACTIONS_PAGE_SIZE - 1
    page1 = [_txn(i) for i in range(WALLET_TRANSACTIONS_PAGE_SIZE)]  # ids 0..boundary_id
    page2 = [_txn(boundary_id)] + [_txn(WALLET_TRANSACTIONS_PAGE_SIZE + i) for i in range(49)]
    pages = [page1, page2]

    def fetch_page(from_id):
        return pages.pop(0) if pages else []

    result = _page_wallet_transactions(fetch_page)

    ids = [t["transaction_id"] for t in result]
    assert ids.count(boundary_id) == 1
    assert len(result) == WALLET_TRANSACTIONS_PAGE_SIZE + 49  # not +50

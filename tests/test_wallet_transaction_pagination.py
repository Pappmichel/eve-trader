import datetime as dt

from eve_trader.trade_reconciliation import WALLET_TRANSACTIONS_PAGE_SIZE, fetch_recent_transactions


def _txn(transaction_id: int, days_ago: int) -> dict:
    date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()
    return {"transaction_id": transaction_id, "date": date, "type_id": 1, "is_buy": True,
            "unit_price": 1.0, "quantity": 1}


class PagingFakeClient:
    """Serves `pages` (a list of pages, each a list of transaction dicts,
    already ordered oldest-page-last like real ESI's from_id cursor) purely
    by call count - real ESI keys pagination off from_id, but since each
    page here already carries the right transaction_ids, we don't need to
    actually inspect from_id to serve the correct next page in a test."""
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def character_wallet_transactions(self, character_id, auth_role, from_id=None):
        self.calls.append(from_id)
        return self.pages[len(self.calls) - 1] if len(self.calls) <= len(self.pages) else []


def test_stops_after_one_page_when_short_page_returned():
    # Fewer than WALLET_TRANSACTIONS_PAGE_SIZE rows - real ESI signal that
    # there's nothing older to fetch, even if all rows are within the window.
    page = [_txn(i, days_ago=1) for i in range(10)]
    client = PagingFakeClient([page])

    result = fetch_recent_transactions(1, "role", client, lookback_days=30)

    assert len(result) == 10
    assert client.calls == [None]


def test_pages_past_first_batch_when_still_within_window():
    # First page is a *full* WALLET_TRANSACTIONS_PAGE_SIZE batch, all recent -
    # a single un-paginated call would stop here and silently miss the older,
    # still-in-window second page (the real-world Oxygen Isotopes bug).
    page1 = [_txn(i, days_ago=1) for i in range(WALLET_TRANSACTIONS_PAGE_SIZE)]
    page2 = [_txn(i, days_ago=5) for i in range(50)]
    client = PagingFakeClient([page1, page2])

    result = fetch_recent_transactions(1, "role", client, lookback_days=30)

    assert len(result) == WALLET_TRANSACTIONS_PAGE_SIZE + 50
    assert len(client.calls) == 2
    # second call's from_id is the oldest (lowest) transaction_id of page 1
    assert client.calls[1] == 0


def test_stops_once_a_page_reaches_past_the_cutoff():
    page1 = [_txn(i, days_ago=1) for i in range(WALLET_TRANSACTIONS_PAGE_SIZE)]
    # Page 2 straddles the cutoff: some rows in-window, some way older -
    # fetching stops after this page (its oldest row is past cutoff), but
    # only the in-window rows make it into the final result.
    page2 = [_txn(WALLET_TRANSACTIONS_PAGE_SIZE, days_ago=10), _txn(WALLET_TRANSACTIONS_PAGE_SIZE + 1, days_ago=90)]
    client = PagingFakeClient([page1, page2])

    result = fetch_recent_transactions(1, "role", client, lookback_days=30)

    assert len(result) == WALLET_TRANSACTIONS_PAGE_SIZE + 1  # page1 + only the 10-days-ago row of page2
    assert len(client.calls) == 2  # never fetches a hypothetical page 3

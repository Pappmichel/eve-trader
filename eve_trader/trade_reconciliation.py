"""Live wallet-transaction paging for Trading's Wallet Transactions view
(`actions.do_wallet_transactions`).

The FIFO matcher that used to live here (reconcile_realized_trades) is
retired: realized profit comes from the shared ledger in
`eve_trader/realized/` and `eve_trader/trade_realized/`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .esi_client import ESIClient


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


WALLET_TRANSACTIONS_PAGE_SIZE = 2500  # ESI's fixed per-call cap for this endpoint


def fetch_recent_transactions(character_id: int, auth_role: str, client: ESIClient,
                               lookback_days: int) -> list[dict]:
    """Pages through character_wallet_transactions via `from_id` (cursor
    pagination, oldest transaction_id of the previous page) until either a
    page's oldest transaction is older than the lookback cutoff, or a
    short page (< WALLET_TRANSACTIONS_PAGE_SIZE) signals there's nothing
    older left at all - a single un-paginated call only ever sees the most
    recent 2500 transactions, which silently dropped older-but-still-in-
    window trades for any character with more transaction volume than that
    within `lookback_days` (confirmed real-world symptom: a frequently-traded
    item like Oxygen Isotopes missing from Realized Trades even though it
    was clearly sold within the lookback window).

    T1-01 follow-up (independent challenge pass, 2026-09-26): dedupes by
    transaction_id across pages - live production logs show recurring
    duplicate-key errors on this same boundary-transaction shape in the
    persisted-snapshot sync path (esi_data/fetchers.py's own
    _page_wallet_transactions, fixed the same way), and this live-fetch
    path shares the identical from_id-cursor pattern - without this, a
    boundary transaction would be listed twice."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    all_txns: list[dict] = []
    seen_ids: set[int] = set()
    from_id: Optional[int] = None
    while True:
        page = client.character_wallet_transactions(character_id, auth_role=auth_role, from_id=from_id)
        if not page:
            break
        # Dedupe per entry, not per page: ESI can repeat a transaction inside
        # one page too (confirmed live 2026-10-17: 2499 unique ids in a page
        # of 2500), which a page-level filter against earlier pages misses.
        for t in page:
            if t["transaction_id"] not in seen_ids:
                seen_ids.add(t["transaction_id"])
                all_txns.append(t)
        oldest = min(page, key=lambda t: t["transaction_id"])
        # len(page) < WALLET_TRANSACTIONS_PAGE_SIZE as the "no more pages"
        # signal relies on ESI's per-call cap staying fixed at 2500 - correct
        # today, but would silently under-page (looking identical to "no
        # older transactions left") if CCP ever lowered it.
        if _parse_iso(oldest["date"]) < cutoff or len(page) < WALLET_TRANSACTIONS_PAGE_SIZE:
            break
        from_id = oldest["transaction_id"]
    in_window = [t for t in all_txns if _parse_iso(t["date"]) >= cutoff]
    for t in in_window:
        t["_wallet_kind"] = "character"
        t["_wallet_owner_id"] = character_id
        t["_wallet_division"] = None
    return in_window

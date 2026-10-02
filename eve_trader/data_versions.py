"""When this tenant's data last changed, per source (FRONTEND_PLAN.md B.12).

Since the scheduler runs again (2026-10-02), data changes without the user
doing anything. The frontend polls `do_data_versions()` and shows "New data
available" when a value moves, instead of re-fetching every page on a timer.
Every value is a cheap MAX()/single-row read; nothing here calls ESI.
"""
from __future__ import annotations

from typing import Optional

from . import storage
from .production import jita_price_cache


def _latest_shortlist_run() -> Optional[str]:
    with storage.connect() as conn:
        row = conn.execute("SELECT MAX(run_ts) FROM shortlist_snapshot").fetchone()
    return row[0] if row else None


def do_data_versions() -> dict[str, Optional[str]]:
    """Opaque version stamps; the frontend only compares them for change."""
    portfolio = storage.latest_portfolio_snapshot_date()
    return {
        "esi": _str(storage.newest_esi_freshness_success_at()),
        "trading_pipeline": _str(storage.get_esi_sync_time("trading")),
        "shortlist": _str(_latest_shortlist_run()),
        "portfolio": portfolio.isoformat() if portfolio else None,
        # Global, shared by every tenant: Jita prices for Production.
        "jita_prices": _str(jita_price_cache.last_updated_at()),
    }


def _str(value) -> Optional[str]:
    return None if value is None else str(value)

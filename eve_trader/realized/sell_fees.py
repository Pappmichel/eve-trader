"""Observed sell fees: what the wallet actually charged on recent market sales.

Both realized-profit reports (Production, Trading) show this next to their
sell-fee setting, so the user can look at the real figure and apply it with
one click. It never changes a setting by itself: a corporation wallet mixes
other members' sales into the ledger, so the owner decides whether the figure
describes their own selling.

The quote is (sales tax + broker fee) / gross revenue over the market sales of
the last `WINDOW_DAYS` days in the report's own channel and owner scope. Broker
fees come from costing's pro-rata attribution (a day's real order fees spread
over that day's order-based fills, buys and sells alike), so the quote is an
estimate, and relist or order-modification fees count only as far as costing
attributed them to a sale. No value is returned without a trustworthy basis;
the caller gets a reason instead.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from . import db

WINDOW_DAYS = 30
MIN_SALES = 20

RELIST_NOTE = (
    "Relist and order-modification fees are included only as far as they are "
    "attributed to a sale, so the real cost of relisting may be higher."
)

REASON_NO_RUN = "no_run"
REASON_NEEDS_REBUILD = "needs_rebuild"
REASON_TOO_FEW_SALES = "too_few_sales"
REASON_NO_BASIS = "no_basis"

_MESSAGES = {
    REASON_NO_RUN: "The realized-profit ledger has not been built yet.",
    REASON_NEEDS_REBUILD: (
        "The shared wallets changed since the last rebuild. Rebuild the "
        "realized-profit ledger first."),
    REASON_TOO_FEW_SALES: "Fewer than {min} market sales with a known sales tax in the last {days} days.",
    REASON_NO_BASIS: "The recent sales give no usable fee rate.",
}


def reason_text(reason: str) -> str:
    return _MESSAGES.get(reason, reason).format(min=MIN_SALES, days=WINDOW_DAYS)


def observed_quote(
    *,
    run,
    sharing_changed: bool,
    scope: dict,
    now: Optional[datetime] = None,
) -> dict:
    """The observed fee quote for one report scope.

    `run` / `sharing_changed` come from the report's `_run_state()`; `scope` is
    its channel/owner filter. `quote` is None when there is no basis, with
    `reason` (a code) and `message` saying why. The fee setting each tool
    applies it to is the caller's business, not this module's."""
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS)
    out: dict = {
        "window_days": WINDOW_DAYS,
        "min_sales": MIN_SALES,
        "sales": 0,
        "quote": None,
        "sales_tax_rate": None,
        "broker_fee_rate": None,
        "reason": None,
        "message": None,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }

    def _no_value(reason: str) -> dict:
        out["reason"] = reason
        out["message"] = reason_text(reason)
        return out

    if run is None:
        return _no_value(REASON_NO_RUN)
    if sharing_changed:
        return _no_value(REASON_NEEDS_REBUILD)
    sales, gross, sales_tax, broker_fee = db.sell_fee_totals(
        start, end,
        channel=scope.get("channel"),
        wallet_owners=scope.get("wallet_owners"),
        contract_owners=scope.get("contract_owners"),
    )
    out["sales"] = sales
    if sales < MIN_SALES:
        return _no_value(REASON_TOO_FEW_SALES)
    if gross <= 0:
        return _no_value(REASON_NO_BASIS)
    quote = (sales_tax + broker_fee) / gross
    # Settings accept 0..1 only; a rate outside it is a data problem, not a fee.
    if not (0.0 < quote < 1.0):
        return _no_value(REASON_NO_BASIS)
    out["quote"] = quote
    out["sales_tax_rate"] = sales_tax / gross
    out["broker_fee_rate"] = broker_fee / gross
    return out

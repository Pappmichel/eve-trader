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

`upper_bound_quote` adds the order fees costing attributed to no fill (a day
with order fees but no order-based fill: relists, new orders that did not sell
that day, buy orders) to the same numerator. Each wallet owner's fees are
split across channels by that owner's share of market gross in this channel
among all channels in the window, so an owner selling in one channel gives all
of them to it and an owner with no market sale in the window gives none. It is
an upper bound, not a sell fee: buy-order fees and stock built up for later
sales push it up. It is shown only, never offered as a value to apply.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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
        "upper_bound_quote": None,
        "order_fees_without_sale": None,
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
    extra = _without_sale_share(start, end, scope)
    upper = (sales_tax + broker_fee + extra) / gross
    out["order_fees_without_sale"] = extra
    out["upper_bound_quote"] = upper if 0.0 < upper < 1.0 else None
    return out


def _fee_days(start: datetime, end: datetime) -> tuple[date, date]:
    """[first, last) UTC days that overlap [start, end)."""
    last = (end - timedelta(microseconds=1)).astimezone(timezone.utc).date()
    return start.astimezone(timezone.utc).date(), last + timedelta(days=1)


def _without_sale_share(start: datetime, end: datetime, scope: dict) -> float:
    """This channel's share of the scope's wallet owners' unattributed order
    fees in the window (see the module docstring)."""
    channel = scope.get("channel")
    first_day, end_day = _fee_days(start, end)
    fees = db.without_sale_fees_by_owner(first_day, end_day, scope.get("wallet_owners"))
    fees = {owner: amount for owner, amount in fees.items() if amount > 0}
    if not fees or not channel:
        return 0.0
    gross = db.market_gross_by_owner_channel(start, end, list(fees))
    total = 0.0
    for owner, amount in fees.items():
        by_channel = gross.get(owner, {})
        all_channels = sum(by_channel.values())
        if all_channels > 0:
            total += amount * by_channel.get(channel, 0.0) / all_channels
    return total

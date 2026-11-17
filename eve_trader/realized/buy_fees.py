"""Observed buy fee quote: what the wallet actually paid in broker fees on
recent market purchases, as a share of their price.

Trading's `jita_buy_broker_fee` is the fee of a standing buy order. Buying
from sell orders pays no broker fee at all, so a wallet that mostly buys
instantly pays much less on average. This quote shows that average next to
the setting (O3-08); like the sell-fee quote it is a suggestion only and is
applied by the user's click through the normal settings save.

Quote = attributed broker fees / purchase price over the market buy fills of
the last `WINDOW_DAYS` days, by the report's wallet owners, at the hub's
solar system. Fees come from costing's pro-rata attribution (a day's real
order fees spread over that day's order-based fills, sells and buys alike), so
it is an estimate. A corporation wallet mixes other members' purchases in.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from . import db

WINDOW_DAYS = 30
MIN_BUYS = 20

REASON_NO_RUN = "no_run"
REASON_NEEDS_REBUILD = "needs_rebuild"
REASON_NO_HUB = "no_hub_system"
REASON_TOO_FEW_BUYS = "too_few_buys"
REASON_NO_BASIS = "no_basis"

_MESSAGES = {
    REASON_NO_RUN: "The realized-profit ledger has not been built yet.",
    REASON_NEEDS_REBUILD: (
        "The shared wallets changed since the last rebuild. Rebuild the "
        "realized-profit ledger first."),
    REASON_NO_HUB: "The hub region is not one of the known trade hubs, so no hub system is known.",
    REASON_TOO_FEW_BUYS: "Fewer than {min} market purchases in the hub system in the last {days} days.",
    REASON_NO_BASIS: "The recent purchases give no usable fee rate.",
}


def reason_text(reason: str) -> str:
    return _MESSAGES.get(reason, reason).format(min=MIN_BUYS, days=WINDOW_DAYS)


def observed_buy_quote(
    *,
    run,
    sharing_changed: bool,
    wallet_owners: list,
    location_ids: Optional[list[int]],
    now: Optional[datetime] = None,
) -> dict:
    """`run` / `sharing_changed` come from the report's `_run_state()` (same
    guards as the sell-fee quote); `location_ids` are the hub system's
    stations and structures, None when the hub is unknown. `suggested` is the
    quote rounded to 4 decimals, None with `reason` and `message` when there
    is no basis."""
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS)
    out: dict = {
        "source": "wallet_purchases",
        "window_days": WINDOW_DAYS,
        "min_buys": MIN_BUYS,
        "buys": 0,
        "purchase_value": 0.0,
        "quote": None,
        "suggested": None,
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
    if not location_ids:
        return _no_value(REASON_NO_HUB)
    buys, materials, fees = db.bought_market_fee_totals(
        start, end, wallet_owners=wallet_owners, location_ids=location_ids,
    )
    out["buys"] = buys
    out["purchase_value"] = round(materials, 2)
    if buys < MIN_BUYS:
        return _no_value(REASON_TOO_FEW_BUYS)
    if materials <= 0:
        return _no_value(REASON_NO_BASIS)
    quote = fees / materials
    # Settings accept 0..1; 0 is a real answer here (every fill instant).
    if not (0.0 <= quote < 1.0):
        return _no_value(REASON_NO_BASIS)
    out["quote"] = quote
    out["suggested"] = round(quote, 4)
    return out

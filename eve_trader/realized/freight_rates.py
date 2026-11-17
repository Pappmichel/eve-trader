"""Observed freight rate per trade hub, from the tenant's finished couriers.

A freight setting (ISK per m3) is a guess until a courier has actually moved
goods. The archive holds the couriers the tenant issued; `costing.group_shipments`
turns the finished ones that end at the home structure into shipments with
`reward / volume`. This module sums those per hub (a shipment belongs to the hub
whose solar system holds its start location) and offers the result as a
suggestion only: nothing here changes a setting, and Realized keeps pricing each
purchase with its own rate (courier rate or configured rate).

The figure is an aggregate. It carries no contract ids, because the route that
serves it needs no tool grant. Only owners that share `contracts` with a
realized-profit tool count (D11, `access.shared_owners`, via the loader).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import hubs, storage
from ..config import TRADING_CONFIG
from ..production.config import PRODUCTION_CONFIG
from . import costing, loader

WINDOW_DAYS = 90
MIN_SHIPMENTS = 3

REASON_NO_HOME = "no_home_structure"
REASON_NO_COURIERS = "no_couriers"
REASON_TOO_FEW = "too_few_shipments"

_MESSAGES = {
    REASON_NO_HOME: "No home structure is configured, so no courier destination is known.",
    REASON_NO_COURIERS: "No finished courier contracts are shared with a realized-profit tool.",
    REASON_TOO_FEW: "Fewer than {min} finished courier shipments from this hub to the home structure in the last {days} days.",
}


def reason_text(reason: str) -> str:
    return _MESSAGES.get(reason, reason).format(min=MIN_SHIPMENTS, days=WINDOW_DAYS)


def home_location_id() -> Optional[int]:
    return PRODUCTION_CONFIG.home_location_id or TRADING_CONFIG.structure_id


def _row(hub: hubs.TradeHub, start: datetime, end: datetime) -> dict:
    return {
        "region_id": hub.region_id,
        "hub": hub.name,
        "source": "courier_contracts",
        "suggested": None,
        "shipments": 0,
        "volume_m3": 0.0,
        "window_days": WINDOW_DAYS,
        "min_shipments": MIN_SHIPMENTS,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "reason": None,
        "message": None,
    }


def observed_freight_all(now: Optional[datetime] = None) -> list[dict]:
    """One row per trade hub, in `hubs.HUBS` order. `suggested` (whole ISK per
    m3) is None with a `reason` and `message` when there is no basis."""
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS)
    rows = [_row(h, start, end) for h in hubs.HUBS]

    def _fill_reason(reason: str) -> list[dict]:
        for r in rows:
            r["reason"], r["message"] = reason, reason_text(reason)
        return rows

    home = home_location_id()
    if home is None:
        return _fill_reason(REASON_NO_HOME)
    couriers = [c for c in loader._load_couriers()
                if c.date_issued is not None and start <= c.date_issued <= end]
    if not couriers:
        return _fill_reason(REASON_NO_COURIERS)
    shipments, _warnings = costing.group_shipments(couriers, home_location_id=home)
    for hub, row in zip(hubs.HUBS, rows):
        here = set(storage.location_ids_in_systems([hub.solar_system_id]))
        mine = [s for s in shipments if s.start_location_id in here]
        volume = sum(s.volume for s in mine)
        reward = sum(s.reward for s in mine)
        row["shipments"] = len(mine)
        row["volume_m3"] = round(volume, 2)
        if len(mine) < MIN_SHIPMENTS or volume <= 0:
            row["reason"], row["message"] = REASON_TOO_FEW, reason_text(REASON_TOO_FEW)
            continue
        row["suggested"] = int(round(reward / volume))
    return rows


def observed_freight(region_id: int, now: Optional[datetime] = None) -> Optional[dict]:
    """The row of one hub, or None for a region that is not a trade hub."""
    return next((r for r in observed_freight_all(now) if r["region_id"] == region_id), None)

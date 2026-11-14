"""Frozen buy prices for Doctrine's production saving (unified plan U9).

A produced item sold through a doctrine contract enters at its production
cost; the Doctrine page shows, for information only, what buying it instead
would have cost on the sale day. ledger_valuation_prices cannot answer that:
it freezes the planner's cheaper value, which is the build cost for anything
worth building. So every rebuild freezes the buy price itself here, per
(type, sale day), for produced draws on doctrine sales that have none yet:

- a sale day of today or yesterday takes the planner's live buy-list price
  (home market or hub plus freight, buy broker fee included);
- an older day (a backfill, or a rebuild after a long gap) takes The Forge
  daily average (the same cached series the opening valuation uses) plus the
  buy broker fee and Production's haul cost per packaged m3. The home market
  has no price history.

Nothing here touches costing or any profit figure.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ..config import TRADING_CONFIG
from ..production.config import PRODUCTION_CONFIG
from . import db

log = logging.getLogger(__name__)

SOURCE_HISTORY = "forge_history"
SOURCE_UNAVAILABLE = "unavailable"
# A sale this many days old or newer is priced with today's live quotes.
LIVE_DAYS = 1
# A history miss on a day at least this old is stored as unavailable; a
# younger one is retried by the next rebuild (the daily average may not be
# published yet).
MISS_MIN_AGE_DAYS = 7


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _live_buy_prices(type_ids: set[int]) -> dict[int, tuple[float, str]]:
    from ..production.engine import planner_buy_prices
    return planner_buy_prices(type_ids, PRODUCTION_CONFIG)


def _haul_volume(type_id: int) -> float:
    from ..production.engine import _haul_volume as engine_haul_volume
    return float(engine_haul_volume(type_id, PRODUCTION_CONFIG) or 0.0)


def _forge_prices(pairs: set[tuple[int, date]], today: date) -> dict[tuple[int, date], float]:
    """The Forge daily average per pair: cached rows first, then the loader's
    own fetch (which caches what it finds), nearest traded day within a week."""
    from . import loader

    cached = db.list_cached_reference_prices(sorted({tid for tid, _d in pairs}))
    found: dict[tuple[int, date], float] = {}
    todo: set[tuple[int, date]] = set()
    for tid, day in pairs:
        hit = cached.get((tid, day))
        if hit is None:
            todo.add((tid, day))
        elif hit[1] != loader._MISSING_SOURCE and hit[0] > 0:
            found[(tid, day)] = float(hit[0])
    if todo:
        found.update(loader._fetch_reference_prices(todo, today))
    return found


def _history_price(avg: float, type_id: int) -> float:
    return (avg * (1 + TRADING_CONFIG.jita_buy_broker_fee)
            + PRODUCTION_CONFIG.haul_cost_per_m3 * _haul_volume(type_id))


def freeze_missing(today: Optional[date] = None) -> int:
    """Freeze a buy price for every produced (type, sale day) on a doctrine
    sale that has none yet. Returns the number of rows written. A price
    source that fails stores nothing for its pairs, so the next rebuild
    retries them."""
    pairs = {(int(tid), _as_date(day)) for tid, day in db.missing_saving_buy_price_pairs()}
    if not pairs:
        return 0
    today = today or datetime.now(timezone.utc).date()
    live_from = today - timedelta(days=LIVE_DAYS)
    live = {pair for pair in pairs if pair[1] >= live_from}
    old = pairs - live
    rows: list[tuple] = []

    if live:
        try:
            values = _live_buy_prices({tid for tid, _d in live})
        except Exception:  # noqa: BLE001 - informational figure; retry next rebuild
            log.exception("realized: live buy prices for production saving failed")
            values = {}
        if values:
            for tid, day in sorted(live):
                hit = values.get(tid)
                if hit is None:
                    rows.append((tid, day, None, SOURCE_UNAVAILABLE))
                else:
                    rows.append((tid, day, float(hit[0]), str(hit[1])))

    if old:
        try:
            averages = _forge_prices(old, today)
        except Exception:  # noqa: BLE001 - informational figure; retry next rebuild
            log.exception("realized: price history for production saving failed")
            averages = None
        if averages is not None:
            miss_before = today - timedelta(days=MISS_MIN_AGE_DAYS)
            for tid, day in sorted(old):
                avg = averages.get((tid, day))
                if avg is not None and avg > 0:
                    rows.append((tid, day, _history_price(avg, tid), SOURCE_HISTORY))
                elif day <= miss_before:
                    rows.append((tid, day, None, SOURCE_UNAVAILABLE))

    return db.upsert_saving_buy_prices(rows)

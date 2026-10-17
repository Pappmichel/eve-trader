"""Freeze the planner's cost per unit the first time a job is archived.

Cache only: Build plan, asset plan, ship-margin cache, discover cache.
A miss is stored as NULL with source "unavailable". This module makes no
network call and must not raise into the ESI sync (the caller still guards it).
"""
from __future__ import annotations

import logging
from typing import Optional

from . import db

log = logging.getLogger(__name__)

SOURCE_BUILD_PLAN = "build_plan"
SOURCE_ASSET_PLAN = "asset_plan"
SOURCE_SHIP_MARGIN = "ship_margin_cache"
SOURCE_DISCOVER = "discover_cache"
SOURCE_UNAVAILABLE = "unavailable"


def _number(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_type_and_cost(row, cost_attr: str):
    if isinstance(row, dict):
        return row.get("type_id"), row.get(cost_attr)
    return getattr(row, "type_id", None), getattr(row, cost_attr, None)


def _from_rows(rows, type_id: int, cost_attr: str, source: str) -> Optional[tuple[float, str]]:
    for row in rows or []:
        tid, cost = _row_type_and_cost(row, cost_attr)
        amount = _number(cost)
        if tid is not None and int(tid) == int(type_id) and amount is not None:
            return amount, source
    return None


def _from_plans(type_id: int) -> Optional[tuple[float, str]]:
    try:
        from ...api.routers.production import peek_cached_unit_build_cost
    except Exception:  # noqa: BLE001
        log.debug("planned cost: plan cache unavailable", exc_info=True)
        return None
    return peek_cached_unit_build_cost(int(type_id))


def _from_ship_margins(type_id: int) -> Optional[tuple[float, str]]:
    try:
        from ..engine import get_cached_ship_margins
        rows = get_cached_ship_margins()
    except Exception:  # noqa: BLE001
        log.debug("planned cost: ship-margin cache unavailable", exc_info=True)
        return None
    return _from_rows(rows, type_id, "build_cost", SOURCE_SHIP_MARGIN)


def _from_discover(type_id: int) -> Optional[tuple[float, str]]:
    try:
        from ..engine import get_cached_discover_results
        rows = get_cached_discover_results(top_n=10**9)
    except Exception:  # noqa: BLE001
        log.debug("planned cost: discover cache unavailable", exc_info=True)
        return None
    return _from_rows(rows, type_id, "build_cost", SOURCE_DISCOVER)


def lookup_planned_unit_cost(type_id: int) -> tuple[Optional[float], str]:
    """(unit cost, source). NULL / unavailable when no cache holds this type."""
    for finder in (_from_plans, _from_ship_margins, _from_discover):
        found = finder(int(type_id))
        if found is not None:
            return found
    return None, SOURCE_UNAVAILABLE


def freeze_for_new_jobs(owner_type: str, owner_id: int, jobs: list[dict]) -> int:
    """Insert one planned-cost row per new job that has a product. Existing
    rows are left alone (ON CONFLICT DO NOTHING)."""
    rows = []
    for job in jobs:
        job_id = job.get("job_id")
        product = job.get("product_type_id")
        if job_id is None or product is None:
            continue
        cost, source = lookup_planned_unit_cost(int(product))
        rows.append((
            owner_type, int(owner_id), int(job_id), int(product), cost, source,
        ))
    return db.insert_planned_costs(rows)

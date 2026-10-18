"""Thin Trading realized-profit actions. The report module owns the numbers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..actions import ActionError
from . import report


def _parse_time(value: str, name: str) -> datetime:
    text = (value or "").strip()
    if not text:
        raise ActionError(f"{name} is required.")
    try:
        parsed = datetime.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(
            text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActionError(f"{name} must be an ISO date or datetime.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _period(days: int | None, start: str | None, end: str | None, since_t0: bool) -> dict:
    if since_t0 and (days is not None or start or end):
        raise ActionError("since_t0 cannot be combined with days or a custom range.")
    now = datetime.now(timezone.utc)
    if since_t0:
        return {"start": None, "end": now, "since_t0": True, "days": None}
    if start or end:
        if not start or not end:
            raise ActionError("A custom range needs both start and end.")
        start_at = _parse_time(start, "start")
        end_at = _parse_time(end, "end")
        if end_at <= start_at:
            raise ActionError("end must be after start.")
        return {"start": start_at, "end": end_at, "since_t0": False, "days": None}
    window = 30 if days is None else int(days)
    if window < 1 or window > 3660:
        raise ActionError("days must be between 1 and 3660.")
    return {"start": now - timedelta(days=window), "end": now, "since_t0": False, "days": window}


def do_get_trading_realized_summary(days: int | None = None, start: str | None = None,
                                    end: str | None = None, since_t0: bool = False) -> dict:
    data = report.summary(**_period(days, start, end, since_t0))
    # The two figures Portfolio used to chart, still the fixed 30-day
    # portfolio_figures() numbers (P3), not the page's selected period.
    figures = report.portfolio_figures()
    data["portfolio_average_margin"] = figures["trading_average_margin"]
    data["portfolio_daily_profit_volatility"] = figures["trading_daily_profit_volatility"]
    return data


def do_get_trading_realized_items(days: int | None = None, start: str | None = None,
                                  end: str | None = None, since_t0: bool = False) -> dict:
    return report.items(**_period(days, start, end, since_t0))


def do_get_trading_realized_item(type_id: int, days: int | None = None, start: str | None = None,
                                 end: str | None = None, since_t0: bool = False) -> dict:
    if int(type_id) <= 0:
        raise ActionError("type_id must be a positive item id.")
    return report.item(type_id=int(type_id), **_period(days, start, end, since_t0))


def do_get_trading_realized_sale(sale_ref: str) -> dict:
    ref = (sale_ref or "").strip()
    if not ref or len(ref) > 200:
        raise ActionError("sale_ref is required.")
    found = report.sale(ref)
    if found is None:
        raise ActionError(f"No realized sale {ref}.")
    return found


def do_get_trading_realized_inventory() -> dict:
    return report.inventory()


def do_get_trading_realized_warnings() -> dict:
    return report.warnings()


def do_start_trading_realized_rebuild() -> dict:
    """The same background rebuild Production uses. One ledger, one job."""
    from .. import pipeline_runner
    return pipeline_runner.start_rebuild_realized()


def do_trading_realized_rebuild_status() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.job_status(
        pipeline_runner.TOOL_PRODUCTION, pipeline_runner.JOB_REBUILD_REALIZED,
    )


def do_compare_trading(days: int = 30) -> dict:
    """Read-only: old realized_trades next to the Trading channel."""
    window = 30 if days is None else int(days)
    if window < 1 or window > 3660:
        raise ActionError("days must be between 1 and 3660.")
    return report.compare_trading(window)

"""Cross-cutting Trading + Production portfolio route - see
eve_trader/portfolio.py. Deliberately its own tiny router/prefix (not folded
into trading.py or production.py) since it's the one place that reads both
tools' data together - keeping it separate makes that cross-cutting nature
explicit instead of hiding it inside one tool's router."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import schemas
from ... import actions, portfolio, scheduler
from ...actions import ActionError

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/overview", response_model=schemas.PortfolioOverview)
def get_portfolio_overview():
    # GitHub issue #65 (found in a full-codebase audit 2026-08-21): now
    # goes through _wrap like every other read endpoint, for consistency -
    # neither this nor do_list_backups below currently raises ActionError,
    # but a future change to either that starts raising one would otherwise
    # silently regress to a raw 500 instead of a clean 400.
    return _wrap(portfolio.portfolio_overview)


@router.get("/scheduler-status")
def get_scheduler_status():
    return scheduler.get_status()


@router.get("/backups")
def get_backups():
    return _wrap(actions.do_list_backups)["rows"]


@router.post("/backups")
def create_backup():
    return _wrap(actions.do_create_backup)

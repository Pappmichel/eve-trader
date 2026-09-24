"""Cross-cutting Trading + Production portfolio route - see
eve_trader/portfolio.py. Deliberately its own tiny router/prefix (not folded
into trading.py or production.py) since it's the one place that reads both
tools' data together - keeping it separate makes that cross-cutting nature
explicit instead of hiding it inside one tool's router."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import schemas
from ... import portfolio
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
    # portfolio_overview doesn't currently raise ActionError, but a future
    # change that starts raising one would otherwise silently regress to a
    # raw 500 instead of a clean 400. Backups moved to the Admin tool
    # (confirmed real misplacement 2026-09-21, see admin.do_create_backup's
    # own docstring) - this router no longer touches actions.py at all.
    return _wrap(portfolio.portfolio_overview)

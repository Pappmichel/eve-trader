"""Cross-cutting Trading + Production portfolio route - see
eve_trader/portfolio.py. Deliberately its own tiny router/prefix (not folded
into trading.py or production.py) since it's the one place that reads both
tools' data together - keeping it separate makes that cross-cutting nature
explicit instead of hiding it inside one tool's router."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    # raw 500 instead of a clean 400. do_get_portfolio_overview takes
    # today's snapshot lazily on the first read of the day (portfolio
    # rework, section 5.4) - a real decision, so it lives in portfolio.py,
    # not here.
    return _wrap(portfolio.do_get_portfolio_overview)


@router.get("/history", response_model=list[schemas.PortfolioSnapshotRow])
def get_portfolio_history(days: Optional[int] = None):
    return _wrap(portfolio.do_get_portfolio_history, days=days)


@router.get("/manual-prices", response_model=list[schemas.ManualItemPriceRow])
def get_manual_item_prices():
    # Storage-only - no live ESI/Goonmetrics.
    return portfolio.do_list_manual_item_prices()["rows"]


class SetManualItemPriceRequest(BaseModel):
    item_name: str
    price: float


@router.post("/manual-prices")
def set_manual_item_price(req: SetManualItemPriceRequest):
    return _wrap(portfolio.do_set_manual_item_price, item_name=req.item_name, price=req.price)


@router.delete("/manual-prices/{type_id}")
def remove_manual_item_price(type_id: int):
    return _wrap(portfolio.do_remove_manual_item_price, type_id=type_id)

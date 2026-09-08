"""Cross-tool routes - thin wrapper around eve_trader/cross_tool.py (do_*),
same _wrap/module-import pattern as every other router (see api/routers/
production.py's own docstring). Today this is exactly the Wareneingang/
hangar-sorting helper - see cross_tool.py's own module docstring for why it
lives outside any single tool's own router, same reasoning as
api/routers/portfolio.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import schemas
from ... import cross_tool
from ...actions import ActionError

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sorting-list", response_model=schemas.SortingList)
def get_sorting_list():
    return _wrap(cross_tool.do_sorting_list)

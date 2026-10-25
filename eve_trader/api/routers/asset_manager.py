"""Asset Manager routes (docs/ASSET_MANAGER_PLAN.md) - thin wrappers around
eve_trader/asset_manager/actions.py (do_*), same _wrap/module-import pattern
as every other router. Gated by the `asset_manager` grant through
`_TOOL_PATH_PREFIXES` in api/app.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from ...actions import ActionError, ConflictError
from ...asset_manager import actions

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _params(
    q: str = "",
    owners: list[str] = Query(default=[]),
    region_id: Optional[int] = None,
    system_id: Optional[int] = None,
    location_id: Optional[int] = None,
    hangar: Optional[str] = None,
    container_item_id: Optional[int] = None,
    category_ids: list[int] = Query(default=[]),
    group_ids: list[int] = Query(default=[]),
    type_ids: list[int] = Query(default=[]),
    sources: list[str] = Query(default=[]),
    hide_badges: list[str] = Query(default=[]),
    stock_only: bool = False,
    group_by: str = "type_location",
    sort: str = "value",
    desc: bool = True,
    page: int = 1,
    page_size: int = 100,
) -> dict:
    return {
        "q": q, "owners": owners, "region_id": region_id, "system_id": system_id,
        "location_id": location_id, "hangar": hangar, "container_item_id": container_item_id,
        "category_ids": category_ids, "group_ids": group_ids, "type_ids": type_ids,
        "sources": sources, "hide_badges": hide_badges, "stock_only": stock_only,
        "group_by": group_by, "sort": sort, "desc": desc, "page": page, "page_size": page_size,
    }


@router.get("/items")
def list_items(params: dict = Depends(_params)):
    return _wrap(actions.do_list_items, **params)


@router.get("/export.csv")
def export_csv(params: dict = Depends(_params)):
    body = _wrap(actions.do_export_csv, **params)
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="assets.csv"'})


@router.get("/export-eve-list", response_class=PlainTextResponse)
def export_eve_list(params: dict = Depends(_params)):
    return _wrap(actions.do_export_eve_list, **params)


@router.get("/freshness")
def freshness():
    return _wrap(actions.do_freshness)


@router.post("/sync")
def start_sync():
    return _wrap(actions.do_start_sync)


@router.get("/sync/status")
def sync_status():
    return _wrap(actions.do_sync_status)

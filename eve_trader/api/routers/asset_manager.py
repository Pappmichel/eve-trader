"""Asset Manager routes (docs/ASSET_MANAGER_PLAN.md) - thin wrappers around
eve_trader/asset_manager/actions.py (do_*), same _wrap/module-import pattern
as every other router. Gated by the `asset_manager` grant through
`_TOOL_PATH_PREFIXES` in api/app.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

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


@router.get("/locations")
def locations(params: dict = Depends(_params)):
    return _wrap(actions.do_locations, **params)


@router.get("/locations/{location_id}/tree")
def location_tree(location_id: int, params: dict = Depends(_params)):
    rest = {k: v for k, v in params.items() if k != "location_id"}
    return _wrap(actions.do_location_tree, location_id=location_id, **rest)


class LocationNameRequest(BaseModel):
    name: str


@router.put("/locations/{location_id}/name")
def set_location_name(location_id: int, req: LocationNameRequest):
    return _wrap(actions.do_set_location_name, location_id=location_id, name=req.name)


@router.delete("/locations/{location_id}/name")
def remove_location_name(location_id: int):
    return _wrap(actions.do_remove_location_name, location_id=location_id)


@router.post("/locations/{location_id}/retry")
def retry_location(location_id: int):
    return _wrap(actions.do_retry_location, location_id=location_id)


@router.get("/ships/{owner_type}/{owner_id}/{item_id}")
def ship(owner_type: str, owner_id: int, item_id: int):
    return _wrap(actions.do_ship, owner_type=owner_type, owner_id=owner_id, item_id=item_id)


class SaveViewRequest(BaseModel):
    name: str
    query: dict


@router.get("/views")
def list_views():
    return _wrap(actions.do_list_views)


@router.post("/views")
def save_view(req: SaveViewRequest):
    return _wrap(actions.do_save_view, name=req.name, query_params=req.query)


@router.delete("/views/{view_id}")
def delete_view(view_id: int):
    return _wrap(actions.do_delete_view, view_id=view_id)


@router.get("/types/{type_id}")
def type_detail(type_id: int):
    return _wrap(actions.do_type_detail, type_id=type_id)


@router.get("/history")
def history(from_date: Optional[str] = None, to_date: Optional[str] = None, by_owner: bool = False):
    return _wrap(actions.do_history, from_date=from_date, to_date=to_date, by_owner=by_owner)


@router.get("/insights")
def insights(min_value: float = 10_000_000.0):
    return _wrap(actions.do_insights, min_value=min_value)


@router.get("/freshness")
def freshness():
    return _wrap(actions.do_freshness)


@router.post("/sync")
def start_sync():
    return _wrap(actions.do_start_sync)


@router.get("/sync/status")
def sync_status():
    return _wrap(actions.do_sync_status)

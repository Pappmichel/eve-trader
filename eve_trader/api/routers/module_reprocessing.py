"""Module Reprocessing Import tool routes. Thin wrappers around
eve_trader/module_reprocessing/actions.py (do_*) and eve_trader/storage.py
(reads), same pattern as api/routers/refining.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schemas
from ...actions import ActionError
from ...module_reprocessing import actions
from ...module_reprocessing.config import MODULE_REPROCESSING_CONFIG
from ... import storage

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------- reads
@router.get("/discover/results", response_model=list[schemas.DiscoveredModuleResult])
def get_discovered_candidates():
    return _wrap(actions.do_get_discovered_candidates)


@router.get("/shortlist/snapshot", response_model=list[schemas.ModuleShortlistRow])
def get_module_shortlist_snapshot():
    df = storage.latest_module_reprocessing_snapshot()
    return [schemas.ModuleShortlistRow(**r) for r in schemas.records(df)]


@router.get("/shortlist/items", response_model=list[schemas.ModuleShortlistItem])
def get_module_shortlist_items():
    return [
        schemas.ModuleShortlistItem(item_id=item_id, item=item, active=active)
        for item_id, item, active in storage.load_module_reprocessing_shortlist()
    ]


@router.get("/settings", response_model=schemas.ModuleReprocessingSettings)
def get_settings():
    return schemas.ModuleReprocessingSettings(
        **{f: getattr(MODULE_REPROCESSING_CONFIG, f) for f in schemas.ModuleReprocessingSettings.model_fields}
    )


@router.get("/esi/sync-time")
def get_esi_sync_time():
    return {"synced_at": storage.get_esi_sync_time("module_reprocessing")}


# ------------------------------------------------------------------ writes
@router.post("/discover")
def discover_candidates():
    return _wrap(actions.do_discover_candidates)


class ShortlistItemIdsBody(BaseModel):
    item_ids: list[int]


@router.post("/shortlist/add")
def add_to_shortlist(body: ShortlistItemIdsBody):
    return _wrap(actions.do_add_to_shortlist, item_ids=body.item_ids)


@router.post("/shortlist/refresh")
def refresh_module_shortlist():
    return _wrap(actions.do_refresh_shortlist)


@router.post("/shortlist/deactivate")
def deactivate_module_shortlist_items(body: ShortlistItemIdsBody):
    return _wrap(actions.do_deactivate_shortlist_items, item_ids=body.item_ids)


@router.post("/shortlist/activate")
def activate_module_shortlist_items(body: ShortlistItemIdsBody):
    return _wrap(actions.do_activate_shortlist_items, item_ids=body.item_ids)


@router.post("/settings")
def update_settings(updates: schemas.ModuleReprocessingSettings):
    return _wrap(actions.do_update_settings, updates=updates.model_dump())

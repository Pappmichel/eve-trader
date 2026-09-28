"""Module Reprocessing Import tool routes. Thin wrappers around
eve_trader/module_reprocessing/actions.py (do_*) and eve_trader/storage.py
(reads), same pattern as api/routers/refining.py."""
from __future__ import annotations

from typing import Optional

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


@router.get("/shopping-list/minerals", response_model=list[schemas.RefinableMineral])
def get_shoppable_minerals():
    return _wrap(actions.do_list_shoppable_minerals)


@router.get("/shopping-list/requirements", response_model=list[schemas.MineralRequirement])
def get_shopping_requirements():
    return _wrap(actions.do_load_module_shopping_requirements)


@router.get("/settings", response_model=schemas.ModuleReprocessingSettings)
def get_settings():
    return schemas.ModuleReprocessingSettings(
        **{f: getattr(MODULE_REPROCESSING_CONFIG, f) for f in schemas.ModuleReprocessingSettings.model_fields}
    )


@router.get("/esi/sync-time")
def get_esi_sync_time():
    return {"synced_at": storage.get_esi_sync_time("module_reprocessing")}


# ------------------------------------------------------------------ writes
class ShortlistItemIdsBody(BaseModel):
    item_ids: list[int]


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


class MineralRequirementsBody(BaseModel):
    requirements: list[schemas.MineralRequirement]


@router.post("/shopping-list/requirements")
def save_shopping_requirements(body: MineralRequirementsBody):
    return _wrap(actions.do_save_module_shopping_requirements,
                 requirements=[r.model_dump() for r in body.requirements])


class OptimizeShoppingListBody(BaseModel):
    # Omitted/null solves the saved requirement list; a supplied list is an
    # ad-hoc solve that deliberately isn't persisted (see the action's docstring).
    requirements: Optional[list[schemas.MineralRequirement]] = None


@router.post("/shopping-list/optimize", response_model=schemas.ModuleShoppingListPlan)
def optimize_shopping_list(body: Optional[OptimizeShoppingListBody] = None):
    requirements = [r.model_dump() for r in body.requirements] if body and body.requirements is not None else None
    return _wrap(actions.do_optimize_module_shopping_list, requirements=requirements)

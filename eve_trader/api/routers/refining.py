"""Ore & Minerals tool routes - GitHub issue #91. Thin wrappers around
eve_trader/refining/actions.py (do_*) and eve_trader/storage.py (reads),
same pattern as api/routers/trading.py - no business logic here."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schemas
from ...actions import ActionError
from ...refining import actions
from ...refining.config import REFINING_CONFIG
from ...refining.constants import implant_options, rig_options, structure_options
from ... import storage

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------- reads
@router.get("/shortlist/snapshot", response_model=list[schemas.OreShortlistRow])
def get_ore_shortlist_snapshot():
    df = storage.latest_ore_snapshot()
    return [schemas.OreShortlistRow(**r) for r in schemas.records(df)]


@router.get("/shortlist/items", response_model=list[schemas.OreShortlistItem])
def get_ore_shortlist_items():
    return [
        schemas.OreShortlistItem(item_id=item_id, item=item, family=family, is_ice=is_ice, active=active)
        for item_id, item, family, is_ice, active in storage.load_ore_shortlist()
    ]


@router.get("/shopping-list/minerals", response_model=list[schemas.RefinableMineral])
def get_refinable_minerals():
    return _wrap(actions.do_list_refinable_minerals)


@router.get("/shopping-list/requirements", response_model=list[schemas.MineralRequirement])
def get_mineral_requirements():
    return _wrap(actions.do_load_mineral_requirements)


@router.get("/settings", response_model=schemas.RefiningSettings)
def get_settings():
    return schemas.RefiningSettings(**{f: getattr(REFINING_CONFIG, f) for f in schemas.RefiningSettings.model_fields})


@router.get("/settings/options")
def get_settings_options():
    return {"structure_types": list(structure_options()), "rig_tiers": list(rig_options()),
            "implants": list(implant_options())}


@router.get("/esi/sync-time")
def get_esi_sync_time():
    return {"synced_at": storage.get_esi_sync_time("refining")}


# ------------------------------------------------------------------ writes
@router.post("/shortlist/add-candidates")
def add_ore_candidates():
    return _wrap(actions.do_add_ore_to_shortlist)


@router.post("/shortlist/refresh")
def refresh_ore_shortlist():
    return _wrap(actions.do_refresh_ore_shortlist)


@router.post("/settings")
def update_settings(updates: schemas.RefiningSettings):
    return _wrap(actions.do_update_settings, updates=updates.model_dump())


class MineralRequirementsBody(BaseModel):
    requirements: list[schemas.MineralRequirement]


@router.post("/shopping-list/requirements")
def save_mineral_requirements(body: MineralRequirementsBody):
    return _wrap(actions.do_save_mineral_requirements,
                 requirements=[r.model_dump() for r in body.requirements])


class OptimizeShoppingListBody(BaseModel):
    # Omitted/null solves the saved requirement list; a supplied list is an
    # ad-hoc solve that deliberately isn't persisted (see the action's docstring).
    requirements: Optional[list[schemas.MineralRequirement]] = None


@router.post("/shopping-list/optimize", response_model=schemas.ShoppingListPlan)
def optimize_shopping_list(body: Optional[OptimizeShoppingListBody] = None):
    requirements = [r.model_dump() for r in body.requirements] if body and body.requirements is not None else None
    return _wrap(actions.do_optimize_mineral_shopping_list, requirements=requirements)


class ReprocessingPasteBody(BaseModel):
    paste: str


@router.post("/reprocessing/quote", response_model=schemas.ReprocessingQuoteResult)
def quote_reprocessing(body: ReprocessingPasteBody):
    return _wrap(actions.do_quote_reprocessing, paste_text=body.paste)

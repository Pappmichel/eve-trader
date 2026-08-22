"""Ore & Minerals tool routes - GitHub issue #91. Thin wrappers around
eve_trader/refining/actions.py (do_*) and eve_trader/storage.py (reads),
same pattern as api/routers/trading.py - no business logic here."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

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

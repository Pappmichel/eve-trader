"""Station Trading tool routes. Thin wrappers around
eve_trader/station_trading/actions.py (do_*) and eve_trader/storage.py
(simple reads), same pattern as api/routers/refining.py - no business logic
here."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schemas
from ...actions import ActionError
from ...station_trading import actions
from ...station_trading.config import STATION_TRADING_CONFIG
from ... import storage

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------- reads
@router.get("/shortlist", response_model=list[schemas.StationTradingShortlistRow])
def get_shortlist():
    return _wrap(actions.do_get_shortlist)


@router.get("/trader-characters")
def get_trader_characters():
    return [
        {"role_key": role, "character_id": cid, "character_name": name}
        for role, cid, name in actions.do_list_trader_characters()
    ]


@router.get("/skills", response_model=list[schemas.SkillSummary])
def get_skill_summary():
    return _wrap(actions.do_get_skill_summary)


@router.get("/settings", response_model=schemas.StationTradingSettings)
def get_settings():
    return schemas.StationTradingSettings(
        **{f: getattr(STATION_TRADING_CONFIG, f) for f in schemas.StationTradingSettings.model_fields})


@router.get("/esi/sync-time")
def get_esi_sync_time():
    return {"synced_at": storage.get_esi_sync_time("station_trading")}


# ------------------------------------------------------------------ writes
@router.post("/shortlist/refresh")
def refresh_shortlist():
    return _wrap(actions.do_refresh_shortlist)


class ShortlistTypeIdsBody(BaseModel):
    type_ids: list[int]


@router.post("/shortlist/deactivate")
def deactivate_shortlist_items(body: ShortlistTypeIdsBody):
    return _wrap(actions.do_deactivate_shortlist_items, type_ids=body.type_ids)


@router.post("/shortlist/activate")
def activate_shortlist_items(body: ShortlistTypeIdsBody):
    return _wrap(actions.do_activate_shortlist_items, type_ids=body.type_ids)


@router.post("/undercut/check", response_model=schemas.StationTradingUndercutCheckResult)
def check_undercut():
    return _wrap(actions.do_check_undercut)


@router.delete("/auth/character/{role_key}")
def remove_trader_character(role_key: str):
    return _wrap(actions.do_remove_trader_character, role_key=role_key)


@router.post("/settings")
def update_settings(updates: schemas.StationTradingSettings):
    return _wrap(actions.do_update_settings, updates=updates.model_dump())

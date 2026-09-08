"""Sorting tool routes - thin wrappers around eve_trader/sorting/actions.py
(do_*), same _wrap/module-import pattern as every other router (see
api/routers/production.py's own docstring).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schemas
from ...actions import ActionError
from ...production.constants import INTAKE_HANGAR_FLAGS
from ...sorting import actions

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


class AddIntakeSourceRequest(BaseModel):
    source_kind: str
    hangar_flag: str
    owner_name: Optional[str] = None
    label: Optional[str] = None


@router.get("/sorting-list", response_model=schemas.SortingList)
def get_sorting_list():
    return _wrap(actions.do_sorting_list)


@router.get("/intake-sources", response_model=schemas.SortingIntakeSourceList)
def list_intake_sources():
    return _wrap(actions.do_list_intake_sources)


@router.post("/intake-sources", response_model=schemas.SortingIntakeSource)
def add_intake_source(req: AddIntakeSourceRequest):
    return _wrap(
        actions.do_add_intake_source,
        source_kind=req.source_kind,
        hangar_flag=req.hangar_flag,
        owner_name=req.owner_name,
        label=req.label,
    )


@router.delete("/intake-sources/{source_id}")
def remove_intake_source(source_id: int):
    return _wrap(actions.do_remove_intake_source, source_id=source_id)


@router.get("/available-characters", response_model=schemas.SortingAvailableCharacters)
def list_available_characters():
    return _wrap(actions.do_list_available_characters)


@router.get("/available-corps", response_model=schemas.SortingAvailableCorps)
def list_available_corps():
    return _wrap(actions.do_list_available_corps)


@router.get("/hangar-division-options")
def get_hangar_division_options():
    # Deliberately INTAKE_HANGAR_FLAGS, not HANGAR_DIVISION_FLAGS: a
    # Wareneingang intake source legitimately includes "Deliveries" (a
    # courier/market delivery lands there) - unlike Production/Doctrine's
    # stock_hangar_flags/stockpile_hangar_flags, which must NOT offer it
    # (see production/constants.py's INTAKE_HANGAR_FLAGS docstring).
    return {"hangar_division_flags": list(INTAKE_HANGAR_FLAGS)}

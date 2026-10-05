"""PI design tool routes (phase 5b/5c) under /api/pi/design - thin wrappers
around eve_trader/pi/design_actions.py. Gated on the `pi` grant through the
/api/pi/ path prefix."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...actions import ActionError
from ...pi import design_actions

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PlanetBody(BaseModel):
    planet_id: Optional[int] = None
    planet_type_id: Optional[int] = None
    radius_km: Optional[float] = Field(default=None, gt=0, le=1_000_000)
    zone: Optional[str] = None
    cc_level: Optional[int] = Field(default=None, ge=0, le=5)


class WaysBody(PlanetBody):
    product_type_id: int


@router.post("/ways")
def post_ways(body: WaysBody):
    return _wrap(design_actions.do_ways_to_build, **body.model_dump())


class MixedBody(PlanetBody):
    assignments: dict[int, int]
    launchpads: int = Field(default=1, ge=1, le=10)
    storages: int = Field(default=0, ge=0, le=10)


@router.post("/mixed-p2")
def post_mixed(body: MixedBody):
    return _wrap(design_actions.do_mixed_p2, **body.model_dump())


class DesignBody(PlanetBody):
    chain: str
    product_type_id: int
    design: Optional[dict[str, Any]] = None


class StorageBody(DesignBody):
    interval_hours: Optional[float] = Field(default=None, gt=0, le=336)


@router.post("/storage-suggestion")
def post_storage(body: StorageBody):
    return _wrap(design_actions.do_storage_suggestion, **body.model_dump())


class GrowBody(DesignBody):
    yield_per_head: Optional[float] = Field(default=None, ge=0)


@router.post("/grow")
def post_grow(body: GrowBody):
    return _wrap(design_actions.do_grow_to_supply, **body.model_dump())


class EditBody(BaseModel):
    template: Any
    edit: dict[str, Any]
    planet_id: Optional[int] = None
    radius_km: Optional[float] = Field(default=None, gt=0, le=1_000_000)


@router.post("/edit")
def post_edit(body: EditBody):
    return _wrap(design_actions.do_edit_layout, **body.model_dump())

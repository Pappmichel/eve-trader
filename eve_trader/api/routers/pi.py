"""Planetary Industry tool routes. Thin wrappers around eve_trader/pi/actions.py
(do_*), same pattern as api/routers/refining.py - no business logic here."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...actions import ActionError
from ...pi import actions as pi_actions

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PlannerBody(BaseModel):
    chain: str
    product_type_id: int
    planet_id: Optional[int] = None
    planet_type_id: Optional[int] = None
    radius_km: Optional[float] = Field(default=None, gt=0, le=1_000_000)
    cc_level: Optional[int] = Field(default=None, ge=0, le=5)
    zone: Optional[str] = None
    owner_tax_rate: Optional[float] = Field(default=None, ge=0, le=1)
    freight_per_m3: Optional[float] = Field(default=None, ge=0)
    yield_per_head: Optional[float] = Field(default=None, ge=0)
    program_hours: Optional[float] = Field(default=None, gt=0, le=336)
    interval_hours: Optional[float] = Field(default=None, gt=0, le=336)
    design: Optional[dict[str, Any]] = None


@router.get("/meta")
def get_meta():
    return _wrap(pi_actions.do_get_meta)


@router.get("/profitability")
def get_profitability(zone: Optional[str] = None, cc_level: Optional[int] = None):
    return _wrap(pi_actions.do_profitability, zone=zone, cc_level=cc_level)


@router.post("/planner")
def post_planner(body: PlannerBody):
    return _wrap(pi_actions.do_planner, **body.model_dump())


@router.get("/chain/{product_type_id}")
def get_chain(product_type_id: int, zone: Optional[str] = None, cc_level: Optional[int] = None):
    return _wrap(pi_actions.do_chain, product_type_id=product_type_id, zone=zone, cc_level=cc_level)


@router.get("/systems")
def search_systems(q: str = ""):
    return _wrap(pi_actions.do_search_systems, query=q)


@router.get("/systems/{system_id}")
def get_system_planets(system_id: int):
    return _wrap(pi_actions.do_system_planets, solar_system_id=system_id)


@router.get("/plans")
def list_plans():
    return _wrap(pi_actions.do_list_plans)


@router.post("/plans")
def create_plan(plan: dict[str, Any]):
    return _wrap(pi_actions.do_save_plan, plan=plan)


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, plan: dict[str, Any]):
    return _wrap(pi_actions.do_save_plan, plan=plan, plan_id=plan_id)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int):
    return _wrap(pi_actions.do_delete_plan, plan_id=plan_id)


@router.get("/settings")
def get_settings():
    return _wrap(pi_actions.do_get_settings)


@router.put("/settings")
def put_settings(updates: dict[str, Any]):
    return _wrap(pi_actions.do_update_settings, updates=updates)

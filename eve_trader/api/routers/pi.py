"""Planetary Industry tool routes. Thin wrappers around eve_trader/pi/actions.py
(do_*), same pattern as api/routers/refining.py - no business logic here."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
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


@router.get("/characters")
def get_characters():
    return _wrap(pi_actions.do_characters)


class SystemAnalysisBody(BaseModel):
    slots: Optional[int] = Field(default=None, ge=1, le=60)
    characters: Optional[int] = Field(default=None, ge=1, le=100)
    cc_level: Optional[int] = Field(default=None, ge=0, le=5)
    owner_tax_rate: Optional[float] = Field(default=None, ge=0, le=1)


@router.post("/systems/{system_id}/analysis")
def post_system_analysis(system_id: int, body: SystemAnalysisBody):
    return _wrap(pi_actions.do_system_analysis, solar_system_id=system_id, **body.model_dump())


@router.get("/production-demand")
def get_production_demand(request: Request):
    """Reads Production's buy list, so it needs the `production` grant too
    (checked against the grants the middleware put on the request - fail
    closed when absent), like the Doctrine skill check (P-40)."""
    keys = getattr(request.state, "tool_keys", None)
    if keys is None or "production" not in keys:
        raise HTTPException(status_code=403, detail="Forbidden - missing tool grant")
    return _wrap(pi_actions.do_production_demand)


class LayoutBody(BaseModel):
    template: Any
    planet_id: Optional[int] = None
    radius_km: Optional[float] = Field(default=None, gt=0, le=1_000_000)
    yield_per_head: Optional[float] = Field(default=None, ge=0)


@router.post("/layouts/validate")
def post_validate_layout(body: LayoutBody):
    return _wrap(pi_actions.do_validate_layout, **body.model_dump())


class GenerateBody(PlannerBody):
    shape: Optional[str] = None
    comment: Optional[str] = Field(default=None, max_length=200)


@router.post("/layouts/generate")
def post_generate_layout(body: GenerateBody):
    data = body.model_dump()
    data.pop("owner_tax_rate", None)
    data.pop("freight_per_m3", None)
    return _wrap(pi_actions.do_generate_layout, **data)


class RetargetBody(BaseModel):
    template: Any
    planet_type_id: Optional[int] = None
    product_type_id: Optional[int] = None
    planet_id: Optional[int] = None
    radius_km: Optional[float] = Field(default=None, gt=0, le=1_000_000)


@router.post("/layouts/retarget")
def post_retarget_layout(body: RetargetBody):
    return _wrap(pi_actions.do_retarget_template, **body.model_dump())


@router.get("/templates")
def list_templates():
    return _wrap(pi_actions.do_list_templates)


@router.get("/templates/{template_id}")
def get_template(template_id: int, planet_id: Optional[int] = None, radius_km: Optional[float] = None):
    return _wrap(pi_actions.do_get_template, template_id=template_id, planet_id=planet_id, radius_km=radius_km)


class TemplateBody(BaseModel):
    template: Any
    name: Optional[str] = Field(default=None, max_length=100)
    source: str = "paste"


@router.post("/templates")
def create_template(body: TemplateBody):
    return _wrap(pi_actions.do_save_template, **body.model_dump())


@router.put("/templates/{template_id}")
def update_template(template_id: int, body: TemplateBody):
    return _wrap(pi_actions.do_save_template, template_id=template_id, **body.model_dump())


@router.delete("/templates/{template_id}")
def delete_template(template_id: int):
    return _wrap(pi_actions.do_delete_template, template_id=template_id)


@router.get("/templates/{template_id}/export")
def export_template(template_id: int, pretty: bool = False):
    return _wrap(pi_actions.do_export_template, template_id=template_id, pretty=pretty)

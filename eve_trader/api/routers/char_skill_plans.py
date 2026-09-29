"""Skill plan routes (Character Management hub, phase 9) - thin wrappers around
character_management/skill_plan_actions.py. Gated on tool_key
`char_skill_plans` via `_TOOL_PATH_PREFIXES` (api/app.py)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...actions import ActionError
from ...character_management import skill_plan_actions as plans

router = APIRouter()


class PlanBody(BaseModel):
    name: str = Field(max_length=plans.MAX_NAME * 2)
    description: str = Field(default="", max_length=plans.MAX_DESCRIPTION * 2)
    text: Optional[str] = Field(default=None, max_length=plans.MAX_IMPORT_CHARS * 2)


class StepBody(BaseModel):
    skill_id: int
    level: int


class OrderBody(BaseModel):
    order: list[StepBody]


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/plans")
def list_plans():
    return _wrap(plans.do_list_plans)


@router.post("/plans")
def create_plan(body: PlanBody):
    return _wrap(plans.do_create_plan, name=body.name, description=body.description, text=body.text)


@router.get("/skills/search")
def search_skills(q: str = ""):
    return _wrap(plans.do_search_skills, query=q)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int):
    return _wrap(plans.do_get_plan, plan_id=plan_id)


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, body: PlanBody):
    return _wrap(plans.do_update_plan, plan_id=plan_id, name=body.name, description=body.description)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int):
    return _wrap(plans.do_delete_plan, plan_id=plan_id)


@router.post("/plans/{plan_id}/steps")
def add_step(plan_id: int, body: StepBody):
    return _wrap(plans.do_add_skill, plan_id=plan_id, skill_id=body.skill_id, level=body.level)


@router.delete("/plans/{plan_id}/steps/{skill_id}/{level}")
def remove_step(plan_id: int, skill_id: int, level: int):
    return _wrap(plans.do_remove_step, plan_id=plan_id, skill_id=skill_id, level=level)


@router.put("/plans/{plan_id}/order")
def reorder(plan_id: int, body: OrderBody):
    return _wrap(plans.do_reorder, plan_id=plan_id, order=[s.model_dump() for s in body.order])


@router.get("/plans/{plan_id}/export")
def export_plan(plan_id: int):
    return _wrap(plans.do_export_plan, plan_id=plan_id)


@router.post("/sync")
def sync():
    return _wrap(plans.do_sync_plans)


@router.get("/plans/{plan_id}/progress")
def progress(plan_id: int):
    return _wrap(plans.do_plan_progress, plan_id=plan_id)

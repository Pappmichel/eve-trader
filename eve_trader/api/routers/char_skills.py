"""Skills routes (Character Management hub, phase 2) - thin wrappers around
character_management/skills_actions.py (do_*), same _wrap/module-import
pattern as every other router (see api/routers/production.py's docstring).
Gated on tool_key `char_skills` via `_TOOL_PATH_PREFIXES` (api/app.py).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...actions import ActionError
from ...character_management import skill_check, skills_actions

router = APIRouter()


class SettingsRequest(BaseModel):
    queue_warning_hours: float


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/overview")
def overview():
    return _wrap(skills_actions.do_skills_overview)


@router.get("/characters/{character_id}")
def character_skills(character_id: int):
    return _wrap(skills_actions.do_character_skills, character_id=character_id)


@router.get("/matrix")
def matrix():
    return _wrap(skills_actions.do_skill_matrix)


@router.post("/sync")
def sync():
    return _wrap(skills_actions.do_sync_char_skills)


@router.get("/warnings")
def warnings():
    return _wrap(skills_actions.do_queue_warnings)


@router.get("/settings")
def get_settings():
    return _wrap(skills_actions.do_get_skills_settings)


@router.post("/settings")
def set_settings(req: SettingsRequest):
    return _wrap(skills_actions.do_set_queue_warning_hours, hours=req.queue_warning_hours)


@router.get("/doctrine-check")
def doctrine_check(request: Request, doctrine_id: Optional[str] = None):
    """Which characters can fly which doctrine fitting. Reads Doctrine's
    fittings AND the characters' skills, so it needs BOTH grants: `char_skills`
    (enforced by the path prefix) and `doctrine` (checked here against the
    grants the middleware put on the request - fail closed when absent)."""
    keys = getattr(request.state, "tool_keys", None)
    if keys is None or "doctrine" not in keys:
        raise HTTPException(status_code=403, detail="Forbidden - missing tool grant")
    return _wrap(skill_check.do_doctrine_skill_check, doctrine_id=doctrine_id)

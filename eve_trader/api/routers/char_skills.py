"""Skills routes (Character Management hub, phase 2) - thin wrappers around
character_management/skills_actions.py (do_*), same _wrap/module-import
pattern as every other router (see api/routers/production.py's docstring).
Gated on tool_key `char_skills` via `_TOOL_PATH_PREFIXES` (api/app.py).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...actions import ActionError
from ...character_management import skills_actions

router = APIRouter()


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

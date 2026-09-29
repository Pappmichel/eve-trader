"""Character Info routes (Character Management hub, phase 1) - thin wrappers
around character_management/info_actions.py (do_*), same _wrap/module-import
pattern as every other router (see api/routers/production.py's docstring).
Gated on tool_key `char_info` via `_TOOL_PATH_PREFIXES` (api/app.py).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...actions import ActionError
from ...character_management import info_actions

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/overview")
def overview():
    return _wrap(info_actions.do_list_character_overview)


@router.get("/characters/{character_id}")
def character_detail(character_id: int):
    return _wrap(info_actions.do_character_detail, character_id=character_id)


@router.post("/sync")
def sync():
    return _wrap(info_actions.do_sync_char_info)

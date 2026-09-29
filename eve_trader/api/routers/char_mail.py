"""Mail routes (Character Management hub, phase 3) - thin wrappers around
character_management/mail_actions.py (do_*), same _wrap/module-import pattern
as every other router (see api/routers/production.py's docstring). Gated on
tool_key `char_mail` via `_TOOL_PATH_PREFIXES` (api/app.py).

`cursors` travels as a JSON string in the query: it is an opaque
{character_id: next_last_mail_id} map the client just hands back.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...actions import ActionError
from ...character_management import mail_actions

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ArchiveRequest(BaseModel):
    character_id: int
    enabled: bool
    confirm_delete: bool = False


class RefreshRequest(BaseModel):
    character_id: Optional[int] = None


@router.get("/folders")
def folders():
    return _wrap(mail_actions.do_list_folders)


@router.get("/mails")
def mails(label_id: Optional[int] = None, character_id: Optional[int] = None, cursors: Optional[str] = None):
    parsed = None
    if cursors:
        try:
            parsed = json.loads(cursors)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="cursors must be a JSON object") from e
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="cursors must be a JSON object")
    return _wrap(mail_actions.do_list_mails, label_id=label_id, character_id=character_id, cursors=parsed)


@router.get("/mails/{character_id}/{mail_id}")
def open_mail(character_id: int, mail_id: int):
    return _wrap(mail_actions.do_open_mail, character_id=character_id, mail_id=mail_id)


@router.get("/search")
def search(q: str, character_id: Optional[int] = None):
    return _wrap(mail_actions.do_search_mail, q=q, character_id=character_id)


@router.get("/archive")
def archive_status():
    return _wrap(mail_actions.do_mail_archive_status)


@router.post("/archive")
def set_archive(req: ArchiveRequest):
    return _wrap(
        mail_actions.do_set_mail_archive,
        character_id=req.character_id, enabled=req.enabled, confirm_delete=req.confirm_delete,
    )


@router.post("/archive/refresh")
def refresh_archive(req: RefreshRequest):
    return _wrap(mail_actions.do_refresh_mail_archive, character_id=req.character_id)

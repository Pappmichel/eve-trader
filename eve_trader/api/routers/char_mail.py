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
from ...character_management import mail_actions, mail_write

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


class RecipientIn(BaseModel):
    type: Optional[str] = None
    id: Optional[int] = None
    name: Optional[str] = None


class SendRequest(BaseModel):
    from_character_id: int
    recipients: list[RecipientIn]
    subject: str
    body: str = ""
    approved_cost: int = 0


class ReadRequest(BaseModel):
    read: bool = True


class LabelsRequest(BaseModel):
    labels: list[int]


class CreateLabelRequest(BaseModel):
    character_id: int
    name: str
    color: Optional[str] = None


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


# ---------------------------------------------------------- write actions
# Every one of these needs the matching capability (mail_send / mail_organize)
# ticked on the Characters page AND a token holding its scope - checked in the
# do_* action, not here. State-changing methods are covered by the global CSRF
# Origin check in api/app.py like every other /api route.
@router.get("/recipients")
def search_recipients(character_id: int, q: str):
    return _wrap(mail_write.do_search_recipients, character_id=character_id, q=q)


@router.post("/send")
def send(req: SendRequest):
    return _wrap(
        mail_write.do_send_mail,
        from_character_id=req.from_character_id,
        recipients=[r.model_dump(exclude_none=True) for r in req.recipients],
        subject=req.subject, body=req.body, approved_cost=req.approved_cost,
    )


@router.post("/mails/{character_id}/{mail_id}/read")
def mark_read(character_id: int, mail_id: int, req: ReadRequest):
    return _wrap(mail_write.do_mark_mail_read, character_id=character_id, mail_id=mail_id, read=req.read)


@router.post("/mails/{character_id}/{mail_id}/labels")
def set_labels(character_id: int, mail_id: int, req: LabelsRequest):
    return _wrap(mail_write.do_set_mail_labels, character_id=character_id, mail_id=mail_id, labels=req.labels)


@router.delete("/mails/{character_id}/{mail_id}")
def delete_mail(character_id: int, mail_id: int):
    return _wrap(mail_write.do_delete_mail, character_id=character_id, mail_id=mail_id)


@router.post("/labels")
def create_label(req: CreateLabelRequest):
    return _wrap(mail_write.do_create_mail_label, character_id=req.character_id, name=req.name, color=req.color)


@router.delete("/labels/{character_id}/{label_id}")
def delete_label(character_id: int, label_id: int):
    return _wrap(mail_write.do_delete_mail_label, character_id=character_id, label_id=label_id)

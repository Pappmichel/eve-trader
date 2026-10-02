"""Notifications routes (Character Management hub, phase 6) - thin wrappers
around character_management/notification_actions.py. Gated on tool_key
`char_notifications` via `_TOOL_PATH_PREFIXES` (api/app.py)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...actions import ActionError
from ...character_management import notification_actions

router = APIRouter()


class ReadRequest(BaseModel):
    character_id: int
    notification_ids: list[int] = Field(min_length=1, max_length=500)
    read: bool = True


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/notifications")
def notifications(
    character_id: Optional[int] = None, type: Optional[str] = None, category: Optional[str] = None,
    unread_only: bool = False, limit: int = notification_actions.DEFAULT_PAGE, offset: int = 0,
):
    return _wrap(
        notification_actions.do_list_notifications, character_id=character_id, type_filter=type,
        category=category, unread_only=unread_only, limit=limit, offset=offset,
    )


@router.get("/notifications/{character_id}/{notification_id}")
def notification(character_id: int, notification_id: int):
    return _wrap(notification_actions.do_get_notification, character_id=character_id, notification_id=notification_id)


@router.post("/read")
def set_read(body: ReadRequest):
    return _wrap(
        notification_actions.do_set_notifications_read, character_id=body.character_id,
        notification_ids=body.notification_ids, read=body.read,
    )


@router.post("/sync")
def sync():
    return _wrap(notification_actions.do_sync_notifications)

"""Contacts & Calendar routes (Character Management hub, phase 8) - thin
wrappers around character_management/contacts_actions.py. Read-only. Gated on
tool_key `char_contacts` via `_TOOL_PATH_PREFIXES` (api/app.py)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...actions import ActionError
from ...character_management import contacts_actions

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/characters")
def characters():
    return _wrap(contacts_actions.do_list_characters)


@router.get("/contacts/{character_id}")
def contacts(character_id: int):
    return _wrap(contacts_actions.do_get_contacts, character_id=character_id)


@router.get("/calendar/{character_id}")
def calendar(character_id: int):
    return _wrap(contacts_actions.do_get_calendar, character_id=character_id)


@router.get("/calendar/{character_id}/{event_id}")
def calendar_event(character_id: int, event_id: int):
    return _wrap(contacts_actions.do_get_calendar_event, character_id=character_id, event_id=event_id)

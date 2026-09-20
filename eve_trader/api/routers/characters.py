"""Characters tool routes — thin wrappers around esi_data.actions do_*
(docs/ESI_ACCESS_PLAN.md Phase 6). The Characters page that consumes
these (confirm-dialog payload, sharing toggles, Admin auto-tick) is
Phase 9.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth
from ...esi_data import actions as esi_actions
from ...esi_data.actions import ActionError

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sharing")
def list_sharing(tool_key: Optional[str] = None):
    return esi_actions.do_list_sharing(tool_key)


@router.get("/freshness")
def list_freshness():
    return esi_actions.do_list_freshness()


@router.get("/capabilities")
def list_capabilities():
    return esi_actions.do_list_capabilities()


@router.get("/owners")
def list_owners():
    return esi_actions.do_list_token_characters()


class SetSharingRequest(BaseModel):
    owner_type: str
    owner_id: int
    data_kind: str
    tool_key: str
    enabled: bool


@router.post("/sharing")
def set_sharing(req: SetSharingRequest):
    return _wrap(
        esi_actions.do_set_sharing,
        owner_type=req.owner_type,
        owner_id=req.owner_id,
        data_kind=req.data_kind,
        tool_key=req.tool_key,
        enabled=req.enabled,
    )


class SetCapabilityRequest(BaseModel):
    character_id: int
    capability_key: str
    enabled: bool


@router.post("/capabilities")
def set_capability(req: SetCapabilityRequest):
    return _wrap(
        esi_actions.do_set_capability,
        character_id=req.character_id,
        capability_key=req.capability_key,
        enabled=req.enabled,
    )


@router.get("/access-preview")
def access_preview(character_id: int, extra_kinds: str = ""):
    kinds = [k for k in extra_kinds.split(",") if k]
    scopes = _wrap(esi_actions.do_reauth_scopes, character_id=character_id, extra_kinds=kinds)
    return _wrap(
        esi_actions.do_access_preview,
        requested_scopes=scopes,
        character_id=character_id,
        title="Confirm ESI access",
    )


@router.get("/reauth/start")
def reauth_start(
    request: Request, response: Response, character_id: int, extra_kinds: str = "",
):
    kinds = [k for k in extra_kinds.split(",") if k]
    scopes = _wrap(esi_actions.do_reauth_scopes, character_id=character_id, extra_kinds=kinds)
    return auth.begin_oauth(
        request, response,
        role_prefix="reauth",
        scopes=scopes,
        extra={"reauth_character_id": character_id},
    )


@router.post("/sync")
def sync(tool_key: Optional[str] = None):
    return _wrap(esi_actions.do_sync, tool_key=tool_key)

"""Admin tool routes - thin wrappers around admin.py's do_* functions. See
that module's own docstring for why this is a deliberate cross-tenant
superadmin surface - AccessGateMiddleware (api/app.py) is what actually
restricts /api/admin/* to characters with the "admin" tool grant (only
storage.DEFAULT_TENANT_ID's own users get that by default, see access_gate.
tools_for); nothing in this router itself re-checks that."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schemas
from ... import admin
from ...actions import ActionError

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tenants", response_model=list[schemas.AdminTenant])
def list_tenants():
    return admin.do_list_tenants()


@router.get("/users", response_model=list[schemas.AdminUser])
def list_users():
    return admin.do_list_users()


class AddUserRequest(BaseModel):
    character_name: str


@router.post("/users")
def add_user(req: AddUserRequest):
    # No response_model=schemas.AdminUser here, deliberately - do_add_user's
    # own return shape (character_id/character_name/tenant_id) doesn't
    # include tenant_name/tool_keys (a freshly-added user has neither
    # resolved nor granted yet) - GET /users is what returns full AdminUser
    # rows.
    return _wrap(admin.do_add_user, character_name=req.character_name)


@router.delete("/users/{character_id}")
def remove_user(character_id: int):
    return _wrap(admin.do_remove_user, character_id=character_id)


class SetToolGrantsRequest(BaseModel):
    tool_keys: list[str]


@router.put("/users/{character_id}/tools")
def set_tool_grants(character_id: int, req: SetToolGrantsRequest):
    return _wrap(admin.do_set_tool_grants, character_id=character_id, tool_keys=req.tool_keys)

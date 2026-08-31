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
from ... import admin, error_log
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


# GitHub issue #34: moved here from /api/production/sde/refresh - the SDE
# cache is global/shared across every tenant, so triggering a refresh is a
# cross-tenant-impacting action, not a per-tenant Production one.
@router.post("/sde/refresh")
def refresh_sde():
    return _wrap(admin.do_refresh_sde)


# Same "cross-tenant-impacting cache, not a per-tenant Production button"
# reasoning as /sde/refresh above - see production/jita_price_cache.py and
# admin.do_refresh_jita_price_cache's own docstrings.
@router.post("/jita-price-cache/refresh")
def refresh_jita_price_cache():
    return _wrap(admin.do_refresh_jita_price_cache)


# GitHub issue #88 - error_log is deliberately its own module (like
# portfolio.py/admin.py's own docstring on cross-cutting concerns), not
# folded into admin.py, since /api/errors' *report* endpoint (api/routers/
# errors.py) needs to stay reachable by every tenant's frontend, unlike
# every other route in this admin-gated router.
@router.get("/errors", response_model=list[schemas.ErrorLogRow])
def list_errors(limit: int = 200):
    return error_log.do_list_errors(limit=limit)

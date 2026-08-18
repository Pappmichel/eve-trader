"""Doctrine tool routes - thin wrappers around doctrine/actions.py (do_*).
No business logic here - see doctrine/actions.py/engine.py/validation.py/
parser.py for that. Same _wrap/module-import conventions as api/routers/
production.py (see that file's own docstring)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schemas
from ...doctrine import actions
from ...doctrine.actions import ActionError
from ...doctrine.config import DOCTRINE_CONFIG

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------- doctrines
@router.get("/doctrines", response_model=list[schemas.DoctrineStatus])
def list_doctrines_with_status():
    return _wrap(actions.do_get_doctrine_status)["doctrines"]


class CreateDoctrineRequest(BaseModel):
    name: str
    description: Optional[str] = None


@router.post("/doctrines", response_model=schemas.Doctrine)
def create_doctrine(req: CreateDoctrineRequest):
    return _wrap(actions.do_create_doctrine, name=req.name, description=req.description)


class UpdateDoctrineRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


@router.patch("/doctrines/{doctrine_id}", response_model=schemas.Doctrine)
def update_doctrine(doctrine_id: str, req: UpdateDoctrineRequest):
    return _wrap(actions.do_update_doctrine, doctrine_id=doctrine_id, **req.model_dump())


@router.delete("/doctrines/{doctrine_id}")
def delete_doctrine(doctrine_id: str):
    return _wrap(actions.do_delete_doctrine, doctrine_id=doctrine_id)


# -------------------------------------------------------------------- fittings
class ParseFittingRequest(BaseModel):
    raw_eft: str


@router.post("/fittings/parse")
def parse_fitting(req: ParseFittingRequest):
    return _wrap(actions.do_parse_fitting, raw_eft=req.raw_eft)


class AddFittingRequest(BaseModel):
    raw_eft: str
    name: Optional[str] = None
    variant_label: Optional[str] = None
    contract_target: int = 0
    stockpile_target: int = 0
    cargo_tolerance_pct: Optional[float] = None


@router.post("/doctrines/{doctrine_id}/fittings")
def add_fitting(doctrine_id: str, req: AddFittingRequest):
    return _wrap(actions.do_add_fitting, doctrine_id=doctrine_id, **req.model_dump())


class UpdateFittingRequest(BaseModel):
    raw_eft: Optional[str] = None
    name: Optional[str] = None
    variant_label: Optional[str] = None
    contract_target: Optional[int] = None
    stockpile_target: Optional[int] = None
    cargo_tolerance_pct: Optional[float] = None
    active: Optional[bool] = None


@router.patch("/fittings/{fitting_id}")
def update_fitting(fitting_id: str, req: UpdateFittingRequest):
    return _wrap(actions.do_update_fitting, fitting_id=fitting_id, **req.model_dump())


@router.delete("/fittings/{fitting_id}")
def delete_fitting(fitting_id: str):
    return _wrap(actions.do_delete_fitting, fitting_id=fitting_id)


@router.get("/fittings/{fitting_id}")
def get_fitting_detail(fitting_id: str):
    return _wrap(actions.do_get_fitting_detail, fitting_id=fitting_id)


# ----------------------------------------------------------------------- sync
@router.post("/sync")
def sync_contracts():
    return _wrap(actions.do_sync_contracts)


@router.post("/validate")
def validate_contracts():
    return _wrap(actions.do_validate_contracts)


@router.get("/sync-time")
def get_sync_time():
    return actions.do_get_esi_sync_time()


# --------------------------------------------------------------------- status
@router.get("/status")
def get_status(doctrine_id: Optional[str] = None):
    return _wrap(actions.do_get_doctrine_status, doctrine_id=doctrine_id)


@router.get("/contracts", response_model=list[schemas.ContractRow])
def list_contracts(fitting_id: Optional[str] = None, status: Optional[str] = None):
    return _wrap(actions.do_list_contracts, fitting_id=fitting_id, status=status)["rows"]


@router.get("/stockpile")
def get_stockpile(doctrine_id: Optional[str] = None):
    return _wrap(actions.do_get_stockpile_status, doctrine_id=doctrine_id)


# ------------------------------------------------------------------ characters
@router.get("/characters")
def get_doctrine_characters():
    return [
        {"role_key": role, "character_id": cid, "character_name": name}
        for role, cid, name in actions.do_list_doctrine_characters()
    ]


@router.post("/auth")
def add_doctrine_character():
    return _wrap(actions.do_auth_doctrine)


@router.delete("/characters/{role_key}")
def remove_doctrine_character(role_key: str):
    return _wrap(actions.do_remove_doctrine_character, role_key=role_key)


# ------------------------------------------------------------------- settings
class DoctrineSettings(BaseModel):
    doctrine_structure_id: Optional[int] = None
    stockpile_location_id: Optional[int] = None
    cargo_tolerance_pct: float
    strict_extras: bool


@router.get("/settings", response_model=DoctrineSettings)
def get_settings():
    return DoctrineSettings(**{f: getattr(DOCTRINE_CONFIG, f) for f in DoctrineSettings.model_fields})


@router.post("/settings")
def update_settings(updates: DoctrineSettings):
    return _wrap(actions.do_update_settings, updates=updates.model_dump())

"""Doctrine tool routes - thin wrappers around doctrine/actions.py (do_*).
No business logic here - see doctrine/actions.py/engine.py/validation.py/
parser.py for that. Same _wrap/module-import conventions as api/routers/
production.py (see that file's own docstring)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import schemas
from ...doctrine import actions
from ...doctrine import realized as doctrine_realized
from ...doctrine.actions import ActionError
from ...actions import ConflictError

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
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
    fuel_bay_text: Optional[str] = None
    ship_maintenance_bay_text: Optional[str] = None


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
    fuel_bay_text: Optional[str] = None
    ship_maintenance_bay_text: Optional[str] = None
    reserve_target: Optional[int] = None


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
@router.get("/fittings/{fitting_id}/assemblable")
def get_fitting_assemblable(fitting_id: str, target_sets: Optional[int] = Query(None, ge=1, le=100_000)):
    return _wrap(actions.do_get_assemblable, fitting_id=fitting_id, target_sets=target_sets)


@router.post("/sync")
def sync_contracts():
    """Starts Doctrine contract sync as a background job and returns
    immediately ({run_id, status: running}). Poll GET .../sync/status;
    a second start while any background job is running is HTTP 409."""
    return _wrap(actions.do_start_sync_contracts)


@router.get("/sync/status")
def sync_status():
    return _wrap(actions.do_sync_status)


@router.post("/validate")
def validate_contracts():
    return _wrap(actions.do_validate_contracts)


@router.get("/sync-time")
def get_sync_time():
    # Storage-only timestamp - no live ESI.
    return actions.do_get_esi_sync_time()


@router.post("/assets/sync")
def sync_assets():
    return _wrap(actions.do_sync_assets)


@router.get("/assets/sync-time")
def get_asset_sync_time():
    # Storage-only timestamp - no live ESI.
    return actions.do_get_asset_sync_time()


# --------------------------------------------------------------------- status
@router.get("/status")
def get_status(doctrine_id: Optional[str] = None):
    return _wrap(actions.do_get_doctrine_status, doctrine_id=doctrine_id)


@router.get("/contracts", response_model=list[schemas.ContractRow])
def list_contracts(fitting_id: Optional[str] = None, status: Optional[str] = None,
                   location_id: Optional[int] = None):
    return _wrap(actions.do_list_contracts, fitting_id=fitting_id, status=status,
                 location_id=location_id)["rows"]


@router.get("/contracts/history", response_model=list[schemas.ContractHistoryRow])
def get_contract_history():
    return _wrap(actions.do_contract_history)["rows"]


@router.get("/stockpile")
def get_stockpile(doctrine_id: Optional[str] = None, location_id: Optional[int] = None):
    return _wrap(actions.do_get_stockpile_status, doctrine_id=doctrine_id, location_id=location_id)


@router.get("/strategic-reserve")
def get_strategic_reserve():
    return _wrap(actions.do_get_strategic_reserve)


@router.get("/strategic-reserve/hangars")
def get_strategic_reserve_hangars():
    return _wrap(actions.do_list_reserve_hangars)


@router.get("/equivalents")
def get_equivalents():
    return _wrap(actions.do_list_equivalent_variants)


class EquivalentExcluded(BaseModel):
    excluded: bool


class EquivalentClassDisabled(BaseModel):
    disabled: bool


class EquivalentAdd(BaseModel):
    target_type_id: int


@router.post("/equivalents/types/{type_id}/excluded")
def set_equivalent_excluded(type_id: int, body: EquivalentExcluded):
    return _wrap(actions.do_set_equivalent_excluded, type_id=type_id, excluded=body.excluded)


@router.post("/equivalents/groups/{type_id}/disabled")
def set_equivalent_group_disabled(type_id: int, body: EquivalentClassDisabled):
    return _wrap(actions.do_set_equivalent_group_disabled, type_id=type_id, disabled=body.disabled)


@router.post("/equivalents/types/{type_id}/group")
def add_equivalent(type_id: int, body: EquivalentAdd):
    return _wrap(actions.do_add_equivalent, type_id=type_id, target_type_id=body.target_type_id)


@router.get("/equivalents/candidates")
def search_equivalent_candidates(target_type_id: int, q: str = ""):
    return _wrap(actions.do_search_equivalent_candidates, target_type_id=target_type_id, query=q)


@router.get("/shopping-list")
def get_shopping_list(doctrine_id: Optional[str] = None):
    return _wrap(actions.do_get_shopping_list, doctrine_id=doctrine_id)


# ------------------------------------------------------------------ characters
@router.get("/characters")
def get_doctrine_characters():
    # Token-store listing (get_record, no refresh) - no live ESI.
    return [
        {"role_key": role, "character_id": cid, "character_name": name}
        for role, cid, name in actions.do_list_doctrine_characters()
    ]


@router.delete("/characters/{role_key}")
def remove_doctrine_character(role_key: str):
    return _wrap(actions.do_remove_doctrine_character, role_key=role_key)


@router.get("/asset-characters")
def get_doctrine_asset_characters():
    # Token-store listing (get_record, no refresh) - no live ESI.
    return [
        {"role_key": role, "character_id": cid, "character_name": name}
        for role, cid, name in actions.do_list_doctrine_asset_characters()
    ]


@router.delete("/asset-characters/{role_key}")
def remove_doctrine_asset_character(role_key: str):
    return _wrap(actions.do_remove_doctrine_asset_character, role_key=role_key)


# ------------------------------------------------------------------- settings
class DoctrineSettings(BaseModel):
    # Several contract structures / stockpile locations; the legacy single
    # ids are folded in by do_get_settings and cleared on save.
    doctrine_structure_ids: tuple[int, ...] = ()
    stockpile_location_ids: tuple[int, ...] = ()
    cargo_tolerance_pct: float
    strict_extras: bool
    accept_equivalent_variants: bool = True
    import_cost_per_m3: float
    hub_region_id: int = 10000002
    stockpile_hangar_flags: tuple[str, ...] = ()
    buyer_character_ids: tuple[int, ...] = ()
    strategic_reserve_owner_type: Optional[str] = None
    strategic_reserve_owner_id: Optional[int] = None
    strategic_reserve_location_id: Optional[int] = None
    strategic_reserve_hangar_flag: Optional[str] = None
    strategic_reserve_container_id: Optional[int] = None
    strategic_reserve_include_hulls: bool = True
    ready_to_list_owner_type: Optional[str] = None
    ready_to_list_owner_id: Optional[int] = None
    ready_to_list_location_id: Optional[int] = None
    ready_to_list_hangar_flag: Optional[str] = None


class LocationOption(BaseModel):
    location_id: int
    name: Optional[str] = None


class DoctrineSettingsView(DoctrineSettings):
    # Read-only: what the tool actually uses (Trading's structure when no
    # contract structure is saved) and the names for the pickers/filters.
    effective_structure_ids: list[int] = []
    effective_stockpile_location_ids: list[int] = []
    contract_structures: list[LocationOption] = []
    stockpile_locations: list[LocationOption] = []


@router.get("/settings", response_model=DoctrineSettingsView)
def get_settings():
    return _wrap(actions.do_get_settings)


@router.post("/settings")
def update_settings(updates: DoctrineSettings):
    body = updates.model_dump()
    # A client that does not send the location lists (or the equivalents
    # switch) must not reset them.
    for key in ("doctrine_structure_ids", "stockpile_location_ids", "accept_equivalent_variants",
                "strategic_reserve_owner_type", "strategic_reserve_owner_id", "strategic_reserve_location_id",
                "strategic_reserve_hangar_flag", "strategic_reserve_container_id",
                "strategic_reserve_include_hulls",
                "ready_to_list_owner_type", "ready_to_list_owner_id", "ready_to_list_location_id",
                "ready_to_list_hangar_flag"):
        if key not in updates.model_fields_set:
            body.pop(key)
    return _wrap(actions.do_update_settings, updates=body)


@router.get("/settings/buyer-character-options")
def get_buyer_character_options():
    return _wrap(actions.do_list_buyer_character_options)


@router.get("/settings/hangar-division-options")
def get_hangar_division_options():
    from ...production.constants import HANGAR_DIVISION_FLAGS
    return {"hangar_division_flags": list(HANGAR_DIVISION_FLAGS)}


# ------------------------------------------------------------- realized profit
@router.get("/realized/summary")
def doctrine_realized_summary(days: Optional[int] = None, start: Optional[str] = None,
                              end: Optional[str] = None, since_t0: bool = False):
    return _wrap(
        doctrine_realized.do_get_doctrine_realized_summary,
        days=days, start=start, end=end, since_t0=since_t0,
    )


@router.get("/realized/fittings")
def doctrine_realized_fittings(days: Optional[int] = None, start: Optional[str] = None,
                               end: Optional[str] = None, since_t0: bool = False):
    return _wrap(
        doctrine_realized.do_get_doctrine_realized_fittings,
        days=days, start=start, end=end, since_t0=since_t0,
    )


@router.get("/realized/contracts")
def doctrine_realized_contracts(days: Optional[int] = None, start: Optional[str] = None,
                                end: Optional[str] = None, since_t0: bool = False):
    return _wrap(
        doctrine_realized.do_get_doctrine_realized_contracts,
        days=days, start=start, end=end, since_t0=since_t0,
    )


@router.get("/realized/contract")
def doctrine_realized_contract(sale_ref: str):
    return _wrap(doctrine_realized.do_get_doctrine_realized_contract, sale_ref=sale_ref)


@router.get("/realized/warnings")
def doctrine_realized_warnings():
    return _wrap(doctrine_realized.do_get_doctrine_realized_warnings)


@router.post("/realized/rebuild")
def doctrine_realized_rebuild():
    return _wrap(doctrine_realized.do_start_doctrine_realized_rebuild)


@router.get("/realized/rebuild/status")
def doctrine_realized_rebuild_status():
    return _wrap(doctrine_realized.do_doctrine_realized_rebuild_status)

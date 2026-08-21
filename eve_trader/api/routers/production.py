"""Production tool routes - thin wrappers around production/actions.py (do_*)
and storage.py reads. No business logic here - see production/actions.py/
engine.py for that.

The last computed Buy/Build plan is cached in-process (module-level, single
local user) so /plan/buy and /plan/build can serve it without recomputing.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schemas
from ... import storage
from ...production import actions
from ...production.actions import ActionError
from ...production.config import PRODUCTION_CONFIG

router = APIRouter()

_last_plan: Optional[dict] = None
_last_asset_plan: Optional[dict] = None


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------- reads
@router.get("/sde/counts")
def get_sde_counts():
    return storage.sde_row_counts()


@router.get("/sde/freshness")
def get_sde_freshness():
    return _wrap(actions.do_check_sde_freshness)


@router.get("/stock-targets", response_model=list[schemas.StockTarget])
def get_stock_targets():
    return [
        schemas.StockTarget(type_id=t, type_name=n, backup_stock=b, home_market_stock=h, jita_market_stock=j)
        for t, n, b, h, j in storage.load_stock_targets()
    ]


@router.get("/manual-stock")
def get_manual_stock():
    return storage.load_manual_stock()


@router.get("/manual-build-buy")
def get_manual_build_buy():
    return storage.load_manual_build_buy()


@router.get("/selected-decryptors")
def get_selected_decryptors():
    return storage.load_selected_decryptors()


@router.get("/plan", response_model=Optional[schemas.ProductionPlan])
def get_last_plan():
    return _last_plan


@router.get("/asset-plan", response_model=Optional[schemas.AssetPlan])
def get_last_asset_plan():
    return _last_asset_plan


@router.get("/market-status", response_model=list[schemas.MarketStatusRow])
def get_market_status():
    return _wrap(actions.do_market_status)["rows"]


@router.get("/stock-value")
def get_stock_value():
    return _wrap(actions.do_stock_value)


@router.get("/jobs", response_model=list[schemas.IndustryJobRow])
def get_current_jobs():
    return actions.do_list_current_jobs()["rows"]


@router.get("/slots", response_model=list[schemas.CharacterSlotRow])
def get_character_slots():
    return actions.do_character_slot_overview()["rows"]


class SetCharacterSlotExcludedRequest(BaseModel):
    excluded: bool


# GitHub issue #39: excludes/includes a character from the shared free-slot
# pool and the asset-optimized build list's slot-splitting.
@router.put("/slots/{character_name}/excluded")
def set_character_slot_excluded(character_name: str, req: SetCharacterSlotExcludedRequest):
    return _wrap(actions.do_set_character_slot_excluded, character_name=character_name, excluded=req.excluded)


@router.get("/blueprints", response_model=list[schemas.OwnedBlueprintRow])
def get_owned_blueprints():
    return actions.do_list_owned_blueprints()["rows"]


@router.get("/producer-characters")
def get_producer_characters():
    return [
        {"role_key": role, "character_id": cid, "character_name": name}
        for role, cid, name in actions.do_list_producer_characters()
    ]


class ProductionSettings(BaseModel):
    component_overbuild: float
    bpc_inventory: float
    market_fees: float
    jita_buy_broker_fee: float
    min_margin: float
    min_daily_profit: float
    haul_cost_per_m3: float
    home_market: Optional[str] = None
    home_location_id: Optional[int] = None
    distribution_source_location_id: Optional[int] = None
    invention_location_id: Optional[int] = None
    reaction_structure_type: str
    reaction_rig_tier: str
    component_structure_type: str
    component_rig_tier: str
    manufacturing_structure_type: str
    manufacturing_rig_tier: str
    encryption_skill_level: int
    datacore_skill_1_level: int
    datacore_skill_2_level: int


@router.get("/settings", response_model=ProductionSettings)
def get_settings():
    return ProductionSettings(**{f: getattr(PRODUCTION_CONFIG, f) for f in ProductionSettings.model_fields})


@router.post("/settings")
def update_settings(updates: ProductionSettings):
    return _wrap(actions.do_update_settings, updates=updates.model_dump())


@router.get("/settings/structure-options")
def get_structure_options():
    from ...production.constants import RIG_TIERS, STRUCTURE_TYPES
    return {"structure_types": list(STRUCTURE_TYPES), "rig_tiers": list(RIG_TIERS)}


@router.get("/settings/systems")
def get_system_settings():
    return {
        "component_system_name": PRODUCTION_CONFIG.component_system_name,
        "component_system_id": PRODUCTION_CONFIG.component_system_id,
        "manufacturing_system_name": PRODUCTION_CONFIG.manufacturing_system_name,
        "manufacturing_system_id": PRODUCTION_CONFIG.manufacturing_system_id,
    }


class SetSystemRequest(BaseModel):
    profile: str
    system_name: str


@router.post("/settings/systems")
def set_system(req: SetSystemRequest):
    return _wrap(actions.do_set_system, profile=req.profile, system_name=req.system_name)


@router.get("/decryptors")
def get_decryptors():
    from ...production.constants import DECRYPTORS
    return list(DECRYPTORS)


@router.get("/job-categories")
def get_job_categories():
    from ...production.constants import JOB_CATEGORIES
    return list(JOB_CATEGORIES)


@router.get("/logistics/locations")
def get_category_locations():
    return storage.load_category_locations()


@router.get("/logistics", response_model=list[schemas.LogisticsRow])
def get_logistics_status():
    if not _last_plan or not _last_plan.get("build_list"):
        raise HTTPException(status_code=400, detail="No build list computed yet. Run 'Compute Buy/Build List' first.")
    from ...production.engine import logistics_status
    return logistics_status(_last_plan["build_list"])


@router.get("/logistics/distribution", response_model=list[schemas.DistributionRow])
def get_distribution_recommendations():
    if not _last_plan or not _last_plan.get("build_list"):
        raise HTTPException(status_code=400, detail="No build list computed yet. Run 'Compute Buy/Build List' first.")
    from ...production.engine import distribution_recommendations
    return distribution_recommendations(_last_plan["build_list"])


@router.get("/logistics/invention", response_model=list[schemas.LogisticsRow])
def get_invention_logistics():
    # Deliberately only checks _last_plan is None (not invention_list's own
    # truthiness, unlike the sibling endpoints above) - an empty
    # invention_list is a real, valid "nothing needs inventing right now"
    # state (no Tech II stock targets configured), not an error.
    if _last_plan is None:
        raise HTTPException(status_code=400, detail="No build list computed yet. Run 'Compute Buy/Build List' first.")
    from ...production.engine import invention_logistics
    return invention_logistics(_last_plan.get("invention_list") or [])


@router.get("/logistics/structure-names")
def get_structure_names():
    """Cached names (storage.structure_names) for every location_id
    currently assigned to a job category, saved as a quick-switch option, or
    configured as the distribution source/home/invention station (GitHub
    issue #21 - these three used to be left out entirely, so Distribution's
    "From" column and the Invention station both showed a raw numeric ID
    forever, never a name, regardless of how many times resolve-structure-
    name was called for a category location) - never makes a live ESI call
    itself, so this is always fast; use POST resolve-structure-name to
    actually (re-)resolve one."""
    location_ids = set(storage.load_category_locations().values())
    for ids in storage.load_category_location_options().values():
        location_ids.update(ids)
    for loc_id in (PRODUCTION_CONFIG.distribution_source_location_id, PRODUCTION_CONFIG.home_location_id,
                   PRODUCTION_CONFIG.invention_location_id):
        if loc_id is not None:
            location_ids.add(loc_id)
    cached = storage.get_cached_structure_names(list(location_ids))
    return {str(loc_id): (name if was_cached else None) for loc_id, (was_cached, name) in cached.items()}


class ResolveStructureNameRequest(BaseModel):
    location_id: int
    force: bool = False


@router.post("/logistics/resolve-structure-name")
def resolve_structure_name(req: ResolveStructureNameRequest):
    return _wrap(actions.do_resolve_structure_name, location_id=req.location_id, force=req.force)


# ------------------------------------------------------------------ actions
@router.post("/sde/refresh")
def refresh_sde():
    return _wrap(actions.do_refresh_sde)


@router.delete("/auth/character/{role_key}")
def remove_producer_character(role_key: str):
    return _wrap(actions.do_remove_producer_character, role_key=role_key)


@router.post("/esi/sync")
def sync_esi():
    return _wrap(actions.do_sync_esi)


@router.get("/esi/sync-time")
def get_esi_sync_time():
    return actions.do_get_esi_sync_time()


@router.post("/unlisted-stock/check", response_model=list[schemas.ProductionUnlistedStockRow])
def check_unlisted_stock():
    return _wrap(actions.do_unlisted_stock)["rows"]


@router.post("/build-candidates/discover", response_model=list[schemas.BuildCandidate])
def discover_build_candidates(top_n: int = 200):
    return _wrap(actions.do_discover_build_candidates, top_n=top_n)["rows"]


@router.get("/margins", response_model=list[schemas.ShipMarginRow])
def get_ship_margins():
    return _wrap(actions.do_get_ship_margins)["rows"]


class ItemMarginSearchRequest(BaseModel):
    item_name: str


@router.post("/margins/search", response_model=schemas.ShipMarginRow)
def search_item_margin(req: ItemMarginSearchRequest):
    return _wrap(actions.do_get_item_margin, item_name=req.item_name)


class AddStockTargetRequest(BaseModel):
    type_name: str
    backup_stock: float = 0
    home_market_stock: Optional[float] = None
    jita_market_stock: Optional[float] = None


@router.post("/stock-targets")
def add_stock_target(req: AddStockTargetRequest):
    return _wrap(actions.do_add_stock_target, **req.model_dump())


class UpdateStockTargetRequest(BaseModel):
    backup_stock: Optional[float] = None
    home_market_stock: Optional[float] = None
    jita_market_stock: Optional[float] = None


@router.patch("/stock-targets/{type_id}")
def update_stock_target(type_id: int, req: UpdateStockTargetRequest):
    return _wrap(actions.do_update_stock_target, type_id=type_id, **req.model_dump())


@router.delete("/stock-targets/{type_id}")
def remove_stock_target(type_id: int):
    return _wrap(actions.do_remove_stock_target, type_id=type_id)


class ManualStockRequest(BaseModel):
    type_id: int
    count: float


@router.post("/manual-stock")
def set_manual_stock(req: ManualStockRequest):
    return _wrap(actions.do_set_manual_stock, type_id=req.type_id, count=req.count)


class ManualBuildBuyRequest(BaseModel):
    type_id: int
    decision: str


@router.post("/manual-build-buy")
def set_manual_build_buy(req: ManualBuildBuyRequest):
    return _wrap(actions.do_set_manual_build_buy, type_id=req.type_id, decision=req.decision)


@router.delete("/manual-build-buy/{type_id}")
def clear_manual_build_buy(type_id: int):
    return _wrap(actions.do_clear_manual_build_buy, type_id=type_id)


class SelectedDecryptorRequest(BaseModel):
    type_id: int
    decryptor: str


@router.post("/selected-decryptors")
def set_selected_decryptor(req: SelectedDecryptorRequest):
    return _wrap(actions.do_set_selected_decryptor, type_id=req.type_id, decryptor=req.decryptor)


@router.delete("/selected-decryptors/{type_id}")
def clear_selected_decryptor(type_id: int):
    return _wrap(actions.do_clear_selected_decryptor, type_id=type_id)


class CategoryLocationRequest(BaseModel):
    category: str
    location_id: int


@router.post("/logistics/locations")
def set_category_location(req: CategoryLocationRequest):
    return _wrap(actions.do_set_category_location, category=req.category, location_id=req.location_id)


@router.delete("/logistics/locations/{category}")
def clear_category_location(category: str):
    return _wrap(actions.do_clear_category_location, category=category)


@router.get("/logistics/location-options")
def get_category_location_options():
    return storage.load_category_location_options()


@router.post("/logistics/location-options")
def add_category_location_option(req: CategoryLocationRequest):
    return _wrap(actions.do_add_category_location_option, category=req.category, location_id=req.location_id)


@router.delete("/logistics/location-options/{category}/{location_id}")
def remove_category_location_option(category: str, location_id: int):
    return _wrap(actions.do_remove_category_location_option, category=category, location_id=location_id)


class InventionEstimateRequest(BaseModel):
    product_name: str
    decryptor_name: Optional[str] = None


@router.post("/invention/estimate", response_model=list[schemas.InventionResult])
def estimate_invention(req: InventionEstimateRequest):
    result = _wrap(actions.do_estimate_invention, product_name=req.product_name,
                    decryptor_name=req.decryptor_name)
    return result["results"]


class MaterialTreeRequest(BaseModel):
    type_name: str
    quantity: float = 1.0


@router.post("/material-tree", response_model=schemas.MaterialTreeNode)
def get_material_tree(req: MaterialTreeRequest):
    return _wrap(actions.do_build_material_tree, type_name=req.type_name, quantity=req.quantity)


class AssetLocationSearchRequest(BaseModel):
    item_name: str


class AssetLocationSearchResponse(BaseModel):
    type_id: int
    type_name: str
    locations: list[schemas.AssetLocationRow]


@router.post("/asset-locations", response_model=AssetLocationSearchResponse)
def search_asset_locations(req: AssetLocationSearchRequest):
    return _wrap(actions.do_search_item_locations, item_name=req.item_name)


@router.post("/plan/refresh")
def refresh_production():
    global _last_plan
    result = _wrap(actions.do_refresh_production)
    plan = result["plan"]
    _last_plan = {
        "inventory": plan["inventory"],
        "buy_list": plan["buy_list"],
        "build_list": plan["build_list"],
        "invention_list": plan["invention_list"],
    }
    return {
        "stock_targets": result["stock_targets"],
        "missing_types": result["missing_types"],
        "buy_entries": result["buy_entries"],
        "build_jobs": result["build_jobs"],
        "invention_needs": len(plan["invention_list"]),
    }


@router.post("/asset-plan/refresh")
def refresh_asset_plan():
    global _last_asset_plan
    result = _wrap(actions.do_refresh_asset_plan)
    _last_asset_plan = result["plan"]
    return {"jobs": result["jobs"]}

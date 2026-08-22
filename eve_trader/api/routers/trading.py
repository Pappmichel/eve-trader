"""Trading tool routes - thin wrappers around eve_trader/actions.py (do_*) and
eve_trader/storage.py (reads the old Streamlit page did directly). No business
logic here - see actions.py/storage.py for that."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schemas
from ... import actions, storage
from ...actions import ActionError
from ...config import TRADING_CONFIG

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------- reads
@router.get("/shortlist/snapshot", response_model=list[schemas.ShortlistRow])
def get_shortlist_snapshot():
    df = storage.latest_snapshot()
    records = schemas.records(df)
    days_remaining = actions.shortlist_skip_deactivation_days()
    for r in records:
        r["days_until_deactivation"] = days_remaining.get(r.get("item_id"))
    return [schemas.ShortlistRow(**r) for r in records]


@router.get("/shortlist/items", response_model=list[schemas.ShortlistItem])
def get_shortlist_items():
    return storage.load_shortlist()


@router.get("/shortlist/trends", response_model=dict[int, schemas.MarginTrend])
def get_shortlist_trends():
    return _wrap(actions.do_shortlist_trends)


@router.get("/candidates/universe", response_model=list[schemas.Candidate])
def get_candidate_universe():
    df = storage.read_table("candidate_universe")
    return [schemas.Candidate(**r) for r in schemas.records(df)]


@router.get("/candidates/focused", response_model=list[schemas.Candidate])
def get_focused_candidates():
    df = storage.read_table("focused_candidates")
    return [schemas.Candidate(**r) for r in schemas.records(df)]


@router.get("/candidates/new", response_model=list[schemas.NewCandidateResult])
def get_new_candidates():
    df = storage.read_table("new_candidates")
    if df.empty:
        return []
    latest = df[df["run_ts"] == df["run_ts"].max()].rename(columns={"add_flag": "add"})
    return [schemas.NewCandidateResult(**r) for r in schemas.records(latest)]


@router.get("/history/type-ids")
def get_history_type_ids():
    # Must be registered before /history/{type_id} - Starlette matches routes
    # in registration order, so the dynamic route would otherwise swallow
    # this literal path and fail trying to parse "type-ids" as an int.
    df = storage.read_table("goonmetrics_history")
    return sorted(df["type_id"].unique().tolist()) if not df.empty else []


@router.get("/history/{type_id}")
def get_price_history(type_id: int):
    df = storage.read_table("goonmetrics_history")
    subset = df[df["type_id"] == type_id].sort_values("date")
    return schemas.records(subset)


@router.get("/trades/realized", response_model=list[schemas.RealizedTrade])
def get_realized_trades():
    df = storage.read_table("realized_trades")
    if df.empty:
        return []
    latest = df[df["run_ts"] == df["run_ts"].max()]
    return [schemas.RealizedTrade(**r) for r in schemas.records(latest)]


class TradingSettings(BaseModel):
    import_cost_per_m3: float
    structure_sell_haircut: float
    min_profit_threshold: float
    min_margin_threshold: float
    skip_grace_period_days: int
    enforce_shortlist_cap: bool
    max_active_shortlist_items: int
    min_hit_rate: float
    min_avg_movement: float
    safe_mode_max_ids: int
    lookback_days: int
    jita_region_id: int
    reference_region_id: int
    structure_id: Optional[int] = None
    buyer_character_name: Optional[str] = None
    seller_character_name: Optional[str] = None


@router.get("/settings", response_model=TradingSettings)
def get_settings():
    return TradingSettings(**{f: getattr(TRADING_CONFIG, f) for f in TradingSettings.model_fields})


@router.post("/settings")
def update_settings(updates: TradingSettings):
    return _wrap(actions.do_update_settings, updates=updates.model_dump())


@router.get("/esi/sync-time")
def get_esi_sync_time():
    return {"synced_at": storage.get_esi_sync_time("trading")}


@router.get("/kpis")
def get_kpis():
    snap = storage.latest_snapshot()
    shortlist_items = storage.load_shortlist()
    new_candidates_df = storage.read_table("new_candidates")
    import_count = int((snap["decision"] == "Import").sum()) if not snap.empty else 0
    own_orders_count = int(snap["own_orders_remaining"].gt(0).sum()) if not snap.empty else 0
    recommended_count = int(
        new_candidates_df[new_candidates_df["run_ts"] == new_candidates_df["run_ts"].max()]["add_flag"].sum()
    ) if not new_candidates_df.empty else 0
    return {
        "shortlist_count": len(shortlist_items),
        "import_candidates": import_count,
        "own_sell_orders": own_orders_count,
        "new_recommendations": recommended_count,
    }


# ------------------------------------------------------------------ actions
@router.post("/universe/build")
def build_universe():
    return _wrap(actions.do_build_universe)


@router.post("/universe/focus")
def build_focused():
    return _wrap(actions.do_build_focused)


@router.post("/candidates/find-new")
def find_new_candidates(safe: bool = True):
    return _wrap(actions.do_find_new_candidates, safe=safe)


@router.post("/shortlist/add-new")
def add_to_shortlist():
    return _wrap(actions.do_add_to_shortlist)


@router.post("/shortlist/refresh")
def refresh_shortlist():
    return _wrap(actions.do_refresh_shortlist)


@router.post("/shortlist/recategorize")
def recategorize_shortlist():
    return _wrap(actions.do_recategorize_shortlist)


@router.post("/candidates/refresh-and-prune")
def refresh_and_prune_candidates(safe: bool = True):
    return _wrap(actions.do_refresh_and_prune_candidates, safe=safe)


@router.post("/seller/unlisted-stock", response_model=list[schemas.UnlistedStockRow])
def check_seller_unlisted_stock():
    return _wrap(actions.do_check_seller_unlisted_stock)["rows"]


@router.post("/seller/undercut", response_model=list[schemas.UndercutRow])
def check_undercut():
    return _wrap(actions.do_check_undercut)["rows"]


@router.post("/trades/reconcile")
def reconcile_trades():
    return _wrap(actions.do_reconcile_trades)


# GitHub issue #46: buyer/seller are multi-character now, same pattern as
# Production's own /producer-characters + /auth/character/{role_key}.
@router.get("/buyer-characters")
def get_buyer_characters():
    return [
        {"role_key": role, "character_id": cid, "character_name": name}
        for role, cid, name in actions.do_list_buyer_characters()
    ]


@router.get("/seller-characters")
def get_seller_characters():
    return [
        {"role_key": role, "character_id": cid, "character_name": name}
        for role, cid, name in actions.do_list_seller_characters()
    ]


@router.delete("/auth/character/{role_key}")
def remove_trading_character(role_key: str):
    return _wrap(actions.do_remove_trading_character, role_key=role_key)


@router.post("/pipeline/run")
def run_pipeline(safe: bool = True, rebuild_universe: bool = False):
    return _wrap(actions.do_pipeline, safe=safe, rebuild_universe=rebuild_universe)

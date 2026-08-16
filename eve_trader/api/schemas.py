"""Pydantic response models mirroring eve_trader/models.py and
production/models.py 1:1 (same field names/types) - the API layer's contract
with the React frontend. `from_attributes=True` lets each model validate
directly from the existing dataclass instances (`Model.model_validate(obj)`),
so actions.py/storage.py/engine.py need zero changes for this migration.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")


def records(df: pd.DataFrame) -> list[dict]:
    """pandas represents SQL NULL as NaN even in otherwise-optional
    int/float/string columns - Pydantic's Optional[...] rejects NaN outright
    (it's not a valid int, and "finite number" validation rejects it for
    float too), so every DataFrame-backed endpoint must sanitize through this
    instead of calling df.to_dict("records") directly."""
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


# --------------------------------------------------------------- trading
class Candidate(_Base):
    item: str
    type_id: int
    volume_m3: float
    category: str
    market_group_path: str
    meta_level: Optional[int] = None


class ShortlistItem(_Base):
    item: str
    item_id: int
    category: str
    volume_m3: float
    active: bool = True
    meta_level: Optional[int] = None


class ShortlistRow(_Base):
    item: str
    category: str
    landed_cost: Optional[float]
    net_sell: Optional[float]
    sell_volume: Optional[float]
    own_orders_remaining: float
    profit_per_unit: Optional[float]
    margin: Optional[float]
    profit_per_m3: Optional[float]
    decision: str
    active: bool
    item_id: int
    volume_m3: float
    jita_sell: Optional[float]
    import_cost: Optional[float]
    meta_level: Optional[int] = None
    # Not part of the underlying shortlist_snapshot row - computed by the
    # router from storage.get_shortlist_skip_since() +
    # TradingConfig.skip_grace_period_days. None unless this item is
    # currently on an unbroken "No market data / Skip" streak (see
    # actions.do_refresh_and_prune_candidates).
    days_until_deactivation: Optional[int] = None


class MarginTrend(_Base):
    recent_avg_margin: float
    baseline_avg_margin: float
    trend_pct: float


class NewCandidateResult(_Base):
    item: str
    category: str
    type_id: int
    volume_m3: float
    paired_days: int
    profitable_days: int
    hit_rate: float
    latest_margin: float
    best_margin: float
    avg_profit_m3: float
    avg_sell_movement: float
    score: float
    recommendation: str
    add: bool
    meta_level: Optional[int] = None


class RealizedTrade(_Base):
    type_id: int
    item: str
    buy_date: str
    buy_qty: int
    buy_unit_price: float
    sell_date: str
    sell_qty: int
    sell_unit_price: float
    matched_qty: int
    realized_profit: float
    margin: float


class UnlistedStockRow(_Base):
    type_id: int
    item: str
    asset_quantity: float
    sell_order_remaining: float
    unlisted_quantity: float


class UndercutRow(_Base):
    type_id: int
    item: str
    my_price: float
    competitor_price: float
    difference: float


# ------------------------------------------------------------ production
class StockTarget(_Base):
    type_id: int
    type_name: str
    backup_stock: float
    home_market_stock: Optional[float] = None
    jita_market_stock: Optional[float] = None


class InventoryRow(_Base):
    type_id: int
    type_name: str
    activity: str
    backup_stock: float
    current_stock: float
    total_missing: float


class BuyListEntry(_Base):
    type_id: int
    type_name: str
    quantity: float
    unit_price: Optional[float]
    total_price: Optional[float]
    on_hand_pct: float = 0.0
    buy_from: Optional[str] = None


class BuildJobEntry(_Base):
    type_id: int
    type_name: str
    blueprint_type_id: int
    activity: str
    quantity: float
    job_runs: int
    job_time_seconds: float
    job_cost: Optional[float]
    decryptor: Optional[str] = None
    job_category: Optional[str] = None


class LogisticsRow(_Base):
    category: str
    location_id: int
    type_id: int
    type_name: str
    needed: float
    available: float
    missing: float


class AssetLocationRow(_Base):
    location_id: int
    location_name: Optional[str] = None
    owner_name: str
    quantity: float


class MarketStatusRow(_Base):
    type_id: int
    type_name: str
    backup_target: float
    backup_current: float
    home_target: Optional[float]
    home_listed: float
    jita_target: Optional[float]
    jita_listed: float


class InventionResult(_Base):
    t1_blueprint_type_id: int
    t1_blueprint_name: str
    product_type_id: int
    product_name: str
    decryptor: str
    probability: float
    output_runs: float
    datacore_cost: float
    decryptor_cost: float
    total_attempt_cost: float
    expected_cost_per_success: Optional[float]
    expected_cost_per_run: Optional[float]
    me: int
    te: int
    material_savings_per_run: float
    net_cost_per_run: Optional[float]


class AssetPlanJob(_Base):
    type_id: int
    type_name: str
    blueprint_type_id: int
    activity: str
    quantity: float
    job_runs: int
    runs_ready_now: int
    job_time_seconds: float
    job_cost: Optional[float]
    decryptor: Optional[str] = None
    job_category: Optional[str] = None
    stock_coverage: Optional[float] = None
    recommended_slots: Optional[int] = None


class InventionNeedRow(_Base):
    type_id: int
    type_name: str
    t1_blueprint_type_id: int
    t1_blueprint_name: str
    decryptor: str
    probability: float
    output_runs: float
    runs_needed: int
    bpcs_needed: int
    recommended_invention_runs: int


class IndustryJobRow(_Base):
    job_id: int
    type_name: str
    activity: str
    runs: int
    quantity: Optional[float]
    status: str
    start_date: Optional[str]
    end_date: Optional[str]
    remaining_seconds: Optional[float]
    installer_name: str
    output_value: Optional[float] = None


class CharacterSlotRow(_Base):
    character_name: str
    job_type: str
    total_slots: int
    used_slots: int
    free_slots: int


class OwnedBlueprintRow(_Base):
    type_id: int
    type_name: str
    is_original: bool
    quantity: int
    material_efficiency: int
    time_efficiency: int
    runs: Optional[int]


class ProductionUnlistedStockRow(_Base):
    """Distinct name from the Trading tool's UnlistedStockRow (same file,
    both schemas live here) - see production/models.py's UnlistedStockRow."""
    type_id: int
    type_name: str
    stock_quantity: float


class BuildCandidate(_Base):
    type_id: int
    type_name: str
    activity: str
    build_cost: float
    margin: float
    daily_movement: float
    potential_daily_profit: float
    meta_level: Optional[int] = None


class MaterialTreeNode(_Base):
    """Recursive - see engine.build_material_tree. Pydantic resolves the
    self-reference in `children` via model_rebuild() below."""
    type_id: int
    type_name: str
    quantity: float
    activity: str
    decryptor: Optional[str] = None
    children: list["MaterialTreeNode"] = []


MaterialTreeNode.model_rebuild()


# ------------------------------------------------------------------ portfolio
class PortfolioOverview(_Base):
    trading_realized_profit: float
    trading_average_margin: float
    trading_daily_profit_volatility: Optional[float] = None
    trading_trade_count: int
    production_stock_value: float
    production_stock_targets_configured: bool
    combined_value: float


# ------------------------------------------------------------ plan payloads
class ProductionPlan(BaseModel):
    inventory: list[InventoryRow]
    buy_list: list[BuyListEntry]
    build_list: list[BuildJobEntry]
    invention_list: list[InventionNeedRow]


class AssetPlan(BaseModel):
    jobs: list[AssetPlanJob]

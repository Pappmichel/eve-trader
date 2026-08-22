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
    # Real average daily sold quantity (GitHub issue #51) - see
    # trade_reconciliation.average_daily_sold_by_type / models.ShortlistRow.
    avg_daily_sold: Optional[float] = None
    # Not part of the underlying shortlist_snapshot row - computed by the
    # router from storage.get_shortlist_skip_since() +
    # TradingConfig.skip_grace_period_days. None unless this item is
    # currently on an unbroken "No market data"/"Skip" streak (see
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
    # GitHub issue #56: these were added to the underlying dataclass
    # (models.UnlistedStockRow) and actions.py by issue #45, but never
    # added here - FastAPI's response_model silently stripped both fields
    # from the JSON response, so the #45 feature never actually reached the
    # frontend. Keep in sync with models.UnlistedStockRow.
    sell_volume: Optional[float] = None
    margin: Optional[float] = None


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
    unit_build_cost: Optional[float]
    decryptor: Optional[str] = None
    job_category: Optional[str] = None
    margin: Optional[float] = None


class LogisticsRow(_Base):
    category: str
    location_id: int
    type_id: int
    type_name: str
    needed: float
    available: float
    missing: float
    pull_from_location_id: Optional[int] = None
    pull_from_available: Optional[float] = None


class DistributionRow(_Base):
    type_id: int
    type_name: str
    from_location_id: int
    to_category: str
    to_location_id: int
    quantity: float


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
    unit_build_cost: Optional[float]
    decryptor: Optional[str] = None
    job_category: Optional[str] = None
    margin: Optional[float] = None
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
    excluded_from_planning: bool = False


class OwnedBlueprintRow(_Base):
    type_id: int
    type_name: str
    is_original: bool
    quantity: int
    material_efficiency: int
    time_efficiency: int
    runs: Optional[int]


class ManualBlueprintCopyCostRow(_Base):
    type_id: int
    type_name: str
    purchase_cost: float
    runs: int
    cost_per_run: float


class ProductionUnlistedStockRow(_Base):
    """Distinct name from the Trading tool's UnlistedStockRow (same file,
    both schemas live here) - see production/models.py's UnlistedStockRow."""
    type_id: int
    type_name: str
    stock_quantity: float
    # GitHub issue #56 - see the Trading UnlistedStockRow's own comment above.
    sell_volume: Optional[float] = None
    margin: Optional[float] = None


class BuildCandidate(_Base):
    type_id: int
    type_name: str
    activity: str
    build_cost: float
    margin: float
    daily_movement: float
    potential_daily_profit: float
    meta_level: Optional[int] = None


class ShipMarginRow(_Base):
    type_id: int
    type_name: str
    activity: str
    home_price: Optional[float] = None
    jita_price: Optional[float] = None
    build_cost: Optional[float] = None
    margin_home: Optional[float] = None
    margin_jita: Optional[float] = None
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


# --------------------------------------------------------------- ore & minerals
class OreShortlistItem(_Base):
    item_id: int
    item: str
    family: str
    is_ice: bool
    active: bool = True


class OreShortlistRow(_Base):
    item_id: int
    item: str
    family: str
    is_ice: bool
    active: bool
    volume_m3: Optional[float]
    landed_cost: Optional[float]
    yield_pct: Optional[float]
    mineral_value: Optional[float]
    refining_tax: Optional[float]
    net_sell: Optional[float]
    sell_listed_qty: Optional[float]
    profit_per_unit: Optional[float]
    margin: Optional[float]
    profit_per_m3: Optional[float]
    decision: str


class ReprocessingQuoteRow(_Base):
    name: str
    quantity: int
    type_id: Optional[int]
    category: str
    sell_as_is_value: Optional[float]
    refined_value: Optional[float]
    mineral_value: Optional[float]
    refining_tax: Optional[float]
    decision: str
    error: Optional[str] = None


class ReprocessingQuoteTotals(_Base):
    reprocess_count: int
    total_mineral_value: float
    total_refined_value: float
    total_sell_as_is_value: float


class ReprocessingQuoteResult(_Base):
    rows: list[ReprocessingQuoteRow]
    totals: ReprocessingQuoteTotals


class RefiningSettings(_Base):
    structure_type: str
    rig_tier: str
    security_status: float
    implant: str
    reprocessing_skill_level: int
    reprocessing_efficiency_skill_level: int
    ore_family_skill_levels: dict[str, int]
    scrapmetal_processing_skill_level: int
    refining_tax_rate: float


# ------------------------------------------------------------------ portfolio
class Doctrine(_Base):
    doctrine_id: str
    name: str
    description: Optional[str] = None
    active: bool
    created_at: Optional[str] = None


class Fitting(_Base):
    fitting_id: str
    doctrine_id: str
    name: str
    hull_type_id: int
    raw_eft: str
    variant_label: Optional[str] = None
    contract_target: int
    stockpile_target: int
    cargo_tolerance_pct: Optional[float] = None
    active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    fuel_bay_text: Optional[str] = None
    ship_maintenance_bay_text: Optional[str] = None


class FittingItem(_Base):
    fitting_id: str
    line_no: int
    slot_section: str
    type_id: int
    quantity: float
    is_offline: bool = False


class ParseIssue(_Base):
    line_no: int
    raw_line: str
    issue_kind: str
    message: str


class DeviationRow(_Base):
    contract_id: int
    type_id: int
    kind: str
    severity: str
    expected_qty: float = 0.0
    actual_qty: float = 0.0


class ContractRow(_Base):
    contract_id: int
    source_role: str
    for_corporation: bool
    status: str
    validation_status: str
    issuer_id: Optional[int] = None
    start_location_id: Optional[int] = None
    title: Optional[str] = None
    price: Optional[float] = None
    date_expired: Optional[str] = None
    matched_fitting_id: Optional[str] = None
    match_score: Optional[float] = None
    synced_at: Optional[str] = None
    source_character_name: Optional[str] = None
    hull_type_id: Optional[int] = None
    hull_name: Optional[str] = None


class ContractHistoryRow(_Base):
    contract_id: int
    source_role: str
    fitting_id: Optional[str] = None
    fitting_name: Optional[str] = None
    hull_type_id: Optional[int] = None
    hull_name: Optional[str] = None
    title: Optional[str] = None
    price: Optional[float] = None
    acceptor_id: Optional[int] = None
    acceptor_name: Optional[str] = None
    date_issued: Optional[str] = None
    date_completed: Optional[str] = None
    source_character_name: Optional[str] = None


class StockpileRow(_Base):
    fitting_id: str
    fitting_name: str
    doctrine_id: str
    doctrine_name: str
    type_id: int
    type_name: str
    slot_section: str
    required_total: float
    available: float
    shortfall: float
    severity: Optional[str] = None


class AggregatedStockpileRow(_Base):
    type_id: int
    type_name: str
    required_total: float
    available: float
    shortfall: float
    severity: Optional[str] = None
    fitting_count: int


class ShoppingListRow(_Base):
    type_id: int
    type_name: str
    shortfall: float
    build_cost: Optional[float] = None
    cj_price: Optional[float] = None
    jita_landed_price: Optional[float] = None
    recommended_source: Optional[str] = None
    total_cost: Optional[float] = None


class FittingStatus(_Base):
    fitting_id: str
    fitting_name: str
    doctrine_id: str
    contract_status: str
    valid_contracts: int
    tolerable_contracts: int
    contract_target: int
    stockpile_status: str
    stockpile_target: int
    worst_stockpile_shortfall_pct: float
    last_synced_at: Optional[str] = None
    assets_available: bool = True
    hull_type_id: int = 0
    hull_name: str = ""
    multibuy_cost: Optional[float] = None


class DoctrineStatus(_Base):
    doctrine_id: str
    doctrine_name: str
    overall: str
    contract_rollup: str
    stockpile_rollup: str
    fittings: list[FittingStatus] = []


class PortfolioOverview(_Base):
    trading_realized_profit: float
    trading_average_margin: float
    trading_daily_profit_volatility: Optional[float] = None
    trading_trade_count: int
    production_stock_value: float
    production_stock_targets_configured: bool
    combined_value: float


# ----------------------------------------------------------------------- admin
class AdminTenant(_Base):
    tenant_id: str
    name: str
    created_at: Optional[str] = None


class AdminUser(_Base):
    character_id: int
    character_name: Optional[str] = None
    tenant_id: str
    tenant_name: str
    tool_keys: list[str] = []


# ------------------------------------------------------------ plan payloads
class ProductionPlan(BaseModel):
    inventory: list[InventoryRow]
    buy_list: list[BuyListEntry]
    build_list: list[BuildJobEntry]
    invention_list: list[InventionNeedRow]


class AssetPlan(BaseModel):
    jobs: list[AssetPlanJob]

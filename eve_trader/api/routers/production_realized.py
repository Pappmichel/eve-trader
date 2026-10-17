"""Production realized-profit routes. Thin wrappers around production.actions.

Gated by the production tool grant via the /api/production/ prefix.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...actions import ConflictError
from ...production import actions
from ...production.actions import ActionError

router = APIRouter()


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ConflictError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=str(e))
    except ActionError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


class RealizedFlags(BaseModel):
    estimated: bool
    price_missing: bool
    assumed_me: bool = False
    assumed_decryptor: bool = False
    coverage: Optional[float] = None
    tax_missing: bool = False
    detail_missing: bool = False
    no_structure_bonus: Optional[bool] = None


class CostStructureLine(BaseModel):
    key: str
    label: str
    amount: float


class RealizedPeriod(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    since_t0: bool
    days: Optional[int] = None


class RealizedStatus(BaseModel):
    empty: bool
    needs_rebuild: bool
    sharing_changed: bool = False
    stale: bool = False
    hint: Optional[str] = None
    cutover_t0: Optional[str] = None
    archive_since: Optional[str] = None
    last_recompute: Optional[str] = None
    last_wallet_sync: Optional[str] = None
    last_jobs_sync: Optional[str] = None
    run_id: Optional[int] = None


class RealizedDay(BaseModel):
    date: str
    revenue: float
    cost: float
    profit: float


class RealizedSummary(BaseModel):
    period: RealizedPeriod
    status: RealizedStatus
    revenue: float
    cost_of_goods_sold: float
    realized_profit: float
    margin: Optional[float] = None
    coverage: Optional[float] = None
    estimated: bool
    units_sold: float
    price_missing: bool
    tax_missing_sales: int
    fees_without_sale: float
    fees_on_other_trading: float
    capital_in_bpos: float
    inventory_at_cost: float
    cancelled_job_losses: float
    failed_invention_losses: float
    manual_costs: float
    manual_revenues: float
    profit_including_manual: float
    cost_structure: list[CostStructureLine]
    by_day: list[RealizedDay]
    balances_as_of_rebuild: list[str]
    flags: RealizedFlags


class ProductRow(BaseModel):
    type_id: int
    type_name: Optional[str] = None
    units_sold: float
    revenue: float
    cost: float
    cost_per_unit: Optional[float] = None
    net_sell_per_unit: Optional[float] = None
    profit: float
    margin: Optional[float] = None
    coverage: Optional[float] = None
    planned_cost_per_unit: Optional[float] = None
    planned_coverage: Optional[float] = None
    planned_difference_per_unit: Optional[float] = None
    warnings: list[str]
    flags: RealizedFlags


class ProductList(BaseModel):
    period: RealizedPeriod
    needs_rebuild: bool = False
    rows: list[ProductRow]


class SaleRow(BaseModel):
    sale_ref: str
    type_id: int
    type_name: Optional[str] = None
    quantity: float
    revenue: float
    cost: float
    profit: float
    coverage: float
    sale_date: Optional[str] = None
    gross: float
    sales_tax: Optional[float] = None
    broker_fee: float
    location_id: Optional[int] = None
    net_price: Optional[float] = None
    warnings: list[str]
    cost_structure: list[CostStructureLine]
    flags: RealizedFlags
    source: Optional[str] = None


class ProductDetail(BaseModel):
    period: RealizedPeriod
    needs_rebuild: bool = False
    product: ProductRow
    sales: list[SaleRow]


class ChainNode(BaseModel):
    kind: str
    origin: Optional[str] = None
    type_id: Optional[int] = None
    type_name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_cost: Optional[float] = None
    unit_price: Optional[float] = None
    reference_price: Optional[float] = None
    date: Optional[str] = None
    end_date: Optional[str] = None
    location_id: Optional[int] = None
    freight: Optional[float] = None
    freight_label: Optional[str] = None
    freight_rate_per_m3: Optional[float] = None
    source_ref: Optional[str] = None
    job_id: Optional[int] = None
    job_ref: Optional[str] = None
    activity_id: Optional[int] = None
    runs: Optional[int] = None
    successful_runs: Optional[int] = None
    state: Optional[str] = None
    job_cost: Optional[float] = None
    facility_id: Optional[int] = None
    output_quantity: Optional[float] = None
    cost_total: Optional[float] = None
    share: Optional[float] = None
    cost_share: Optional[float] = None
    sale_ref: Optional[str] = None
    source: Optional[str] = None
    flags: RealizedFlags
    children: list["ChainNode"] = []


class SaleChain(BaseModel):
    sale: SaleRow
    tree: ChainNode


ChainNode.model_rebuild()


class JobRow(BaseModel):
    job_ref: str
    job_id: int
    owner_type: str
    owner_id: int
    activity_id: int
    blueprint_type_id: int
    blueprint_name: Optional[str] = None
    product_type_id: Optional[int] = None
    product_name: Optional[str] = None
    runs: int
    successful_runs: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    state: str
    job_cost: float
    input_cost: float
    unit_cost: Optional[float] = None
    output_quantity: float
    output_quantity_left: Optional[float] = None
    output_unit: Optional[str] = None
    sold_share: Optional[float] = None
    cost_per_bpc_run: Optional[float] = None
    facility_id: Optional[int] = None
    contract_input: bool = False
    cost_structure: list[CostStructureLine]
    flags: RealizedFlags


class JobList(BaseModel):
    cutover_t0: Optional[str] = None
    needs_rebuild: bool = False
    rows: list[JobRow]


class InventoryRow(BaseModel):
    type_id: int
    type_name: Optional[str] = None
    unit: str
    quantity: float
    cost_per_unit: Optional[float] = None
    total: float
    origin_mix: dict[str, float]
    esi_stock: Optional[float] = None
    drift: Optional[float] = None
    flags: RealizedFlags


class InventoryList(BaseModel):
    needs_rebuild: bool = False
    rows: list[InventoryRow]


class WarningGroup(BaseModel):
    kind: str
    count: int
    items: list[dict]


class WarningList(BaseModel):
    needs_rebuild: bool = False
    groups: list[WarningGroup]


class OpeningRow(BaseModel):
    type_id: int
    type_name: Optional[str] = None
    raw_quantity: float
    quantity: float
    reference_price: Optional[float] = None
    unit_cost: float
    price_source: str
    override_unit_cost: Optional[float] = None
    frozen_at: Optional[str] = None
    flags: RealizedFlags


class OpeningList(BaseModel):
    cutover_t0: Optional[str] = None
    needs_rebuild: bool = False
    rows: list[OpeningRow]


class LedgerEntryRow(BaseModel):
    entry_id: int
    entry_kind: str
    type_id: Optional[int] = None
    type_name: Optional[str] = None
    quantity: Optional[float] = None
    amount: float
    entry_date: Optional[str] = None
    note: Optional[str] = None
    created_at: Optional[str] = None


class LedgerEntryList(BaseModel):
    rows: list[LedgerEntryRow]


class ArchiveOwnerRow(BaseModel):
    owner_type: str
    owner_id: int
    shared: dict[str, bool]
    last_sync: dict[str, Optional[str]]
    gaps: dict[str, int]
    hidden_kinds: list[str]


class ArchiveStatus(BaseModel):
    rows: list[ArchiveOwnerRow]


class RebuildStarted(BaseModel):
    run_id: str
    status: str
    job_name: str
    tool: str


class LedgerEntryRequest(BaseModel):
    entry_kind: str
    amount: float
    entry_date: str
    type_id: Optional[int] = None
    quantity: Optional[float] = None
    note: Optional[str] = Field(default=None, max_length=500)


class OpeningCostRequest(BaseModel):
    type_id: int
    unit_cost: float
    entry_date: Optional[str] = None


@router.post("/realized/rebuild", response_model=RebuildStarted)
def start_realized_rebuild(reset_opening: bool = False):
    return _wrap(actions.do_start_rebuild_realized, reset_opening=reset_opening)


@router.get("/realized/rebuild/status")
def realized_rebuild_status():
    return _wrap(actions.do_realized_rebuild_status)


@router.get("/realized/summary", response_model=RealizedSummary)
def realized_summary(days: Optional[int] = None, start: Optional[str] = None,
                     end: Optional[str] = None, since_t0: bool = False):
    return _wrap(actions.do_get_realized_summary, days=days, start=start, end=end, since_t0=since_t0)


@router.get("/realized/products", response_model=ProductList)
def realized_products(days: Optional[int] = None, start: Optional[str] = None,
                      end: Optional[str] = None, since_t0: bool = False):
    return _wrap(actions.do_get_realized_products, days=days, start=start, end=end, since_t0=since_t0)


@router.get("/realized/products/{type_id}", response_model=ProductDetail)
def realized_product(type_id: int, days: Optional[int] = None, start: Optional[str] = None,
                     end: Optional[str] = None, since_t0: bool = False):
    return _wrap(actions.do_get_realized_product, type_id=type_id, days=days, start=start,
                 end=end, since_t0=since_t0)


@router.get("/realized/sale-chain", response_model=SaleChain)
def realized_sale_chain(sale_ref: str):
    return _wrap(actions.do_get_realized_sale_chain, sale_ref=sale_ref)


@router.get("/realized/jobs", response_model=JobList)
def realized_jobs():
    return _wrap(actions.do_get_realized_jobs)


@router.get("/realized/inventory", response_model=InventoryList)
def realized_inventory():
    return _wrap(actions.do_get_realized_inventory)


@router.get("/realized/warnings", response_model=WarningList)
def realized_warnings():
    return _wrap(actions.do_get_realized_warnings)


@router.get("/realized/opening", response_model=OpeningList)
def realized_opening():
    return _wrap(actions.do_get_realized_opening)


@router.get("/realized/archive-status", response_model=ArchiveStatus)
def realized_archive_status():
    return _wrap(actions.do_get_realized_archive_status)


@router.get("/realized/entries", response_model=LedgerEntryList)
def list_realized_entries():
    return _wrap(actions.do_list_ledger_entries)


@router.delete("/realized/entries/{entry_id}")
def delete_realized_entry(entry_id: int):
    return _wrap(actions.do_delete_ledger_entry, entry_id=entry_id)


@router.post("/realized/entries")
def add_realized_entry(req: LedgerEntryRequest):
    return _wrap(
        actions.do_add_ledger_entry, entry_kind=req.entry_kind, amount=req.amount,
        entry_date=req.entry_date, type_id=req.type_id, quantity=req.quantity, note=req.note,
    )


@router.post("/realized/opening-cost")
def set_realized_opening_cost(req: OpeningCostRequest):
    return _wrap(
        actions.do_set_opening_cost, type_id=req.type_id, unit_cost=req.unit_cost,
        entry_date=req.entry_date,
    )


@router.post("/realized/freight/reapply")
def reapply_realized_freight(dry_run: bool = False):
    return _wrap(actions.do_reapply_freight_rate, dry_run=dry_run)


class CourierMonth(BaseModel):
    month: str
    count: int
    volume_m3: float
    reward: float
    paid_isk_per_m3: Optional[float] = None
    configured_rate_per_m3: float


class CourierComparison(BaseModel):
    home_location_id: Optional[int] = None
    configured_rate_per_m3: float
    months: list[CourierMonth]


class HiddenArchiveCount(BaseModel):
    owner_type: str
    owner_id: int
    kind: str
    count: int


class HiddenArchiveDeleted(BaseModel):
    owner_type: str
    owner_id: int
    kind: str
    deleted: int


@router.get("/realized/courier", response_model=CourierComparison)
def realized_courier():
    return _wrap(actions.do_get_realized_courier)


@router.get("/realized/archive/{owner_type}/{owner_id}/{kind}", response_model=HiddenArchiveCount)
def count_hidden_archive(owner_type: str, owner_id: int, kind: str):
    return _wrap(
        actions.do_count_hidden_archive_rows,
        owner_type=owner_type, owner_id=owner_id, kind=kind,
    )


@router.delete("/realized/archive/{owner_type}/{owner_id}/{kind}", response_model=HiddenArchiveDeleted)
def delete_hidden_archive(owner_type: str, owner_id: int, kind: str):
    return _wrap(
        actions.do_delete_hidden_archive_rows,
        owner_type=owner_type, owner_id=owner_id, kind=kind,
    )

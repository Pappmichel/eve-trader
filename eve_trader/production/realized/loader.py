"""Thin I/O layer around pure `costing.rebuild_costing`.

Reads the archive through `access.py` (D11), fills and caches reference
prices in `ledger_reference_prices` (D4/D5/D8), builds a `LedgerSnapshot`,
runs the pure engine, and replaces the derived tables. No business logic
beyond assembling inputs the plan already names.

Expected scale (plan Phase 0 / lessons): the C-J seller alone can have
~12,000 wallet transactions / 30 days and ~10,000 journal entries / week.
All archive reads here are one query per table for the shared-owner set
(not per-row round trips). Reference-price history is fetched once per
missing (type) batch and frozen; a recompute never re-fetches a cached day.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from ... import storage
from ...config import TRADING_CONFIG
from ...goonmetrics_client import GoonmetricsClient
from ..constants import (
    ACTIVITY_INVENTION,
    ACTIVITY_MANUFACTURING,
    ACTIVITY_REACTION,
)
from . import access, db
from .costing import (
    IndustryJob,
    JobInput,
    JournalEntry,
    LedgerSnapshot,
    MarketOrder,
    WalletTransaction,
    rebuild_costing,
)

log = logging.getLogger(__name__)

# The Forge daily average is the reference price (plan section 6).
_REFERENCE_REGION_ID = db.THE_FORGE_REGION_ID

# SDE category for blueprints - excluded from ESI stock for opening (items only).
_BLUEPRINT_CATEGORY_ID = 9


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return _as_utc(value).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _production_type_ids(job_rows: list[tuple]) -> frozenset[int]:
    """Products of ledger jobs + stock targets + integrated special orders."""
    ids: set[int] = set()
    # product_type_id is index 6 in list_industry_jobs_for_owners
    for row in job_rows:
        if row[6] is not None:
            ids.add(int(row[6]))
    for type_id, *_rest in storage.load_stock_targets():
        ids.add(int(type_id))
    for _oid, _note, type_id, _name, _qty in storage.list_integrated_special_order_items():
        ids.add(int(type_id))
    return frozenset(ids)


def _tenant_character_ids() -> frozenset[int]:
    tenant_id = storage.get_current_tenant()
    entries = storage.list_tenant_registry_entries(str(tenant_id))
    return frozenset(int(eid) for et, eid in entries if et == "character")


def _blueprint_type_ids(type_ids: set[int]) -> set[int]:
    """Subset of type_ids that are blueprints (SDE category 9)."""
    if not type_ids:
        return set()
    out: set[int] = set()
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT type_id FROM sde_types "
            "WHERE type_id = ANY(?) AND category_id = ?",
            (list(type_ids), _BLUEPRINT_CATEGORY_ID),
        ).fetchall()
    for r in rows:
        out.add(int(r[0]))
    return out


def _relic_type_ids() -> frozenset[int]:
    """Invention 'blueprint' types that are not blueprints (T3 relics).

    Invention jobs name these as blueprint_type_id but consume them as items,
    not BPC runs (costing.LedgerSnapshot.relic_type_ids).
    """
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT p.blueprint_type_id "
            "FROM sde_blueprint_products p "
            "JOIN sde_types t ON t.type_id = p.blueprint_type_id "
            "WHERE p.activity_id = ? AND t.category_id <> ?",
            (ACTIVITY_INVENTION, _BLUEPRINT_CATEGORY_ID),
        ).fetchall()
    return frozenset(int(r[0]) for r in rows)


def _output_quantity(job_row: tuple) -> Optional[float]:
    """runs (or successful_runs) x SDE product qty for manufacturing/reaction."""
    activity_id = int(job_row[3])
    if activity_id == 9:
        activity_id = ACTIVITY_REACTION
    if activity_id not in (ACTIVITY_MANUFACTURING, ACTIVITY_REACTION):
        return None
    blueprint_type_id = int(job_row[5])
    product_type_id = job_row[6]
    runs = int(job_row[7] or 0)
    successful = job_row[9]
    qty_runs = int(successful) if successful is not None else runs
    if product_type_id is None or qty_runs <= 0:
        return None
    per = storage.get_product_quantity(blueprint_type_id, activity_id, int(product_type_id))
    if per is None:
        return float(qty_runs)
    return float(qty_runs) * float(per)


def _ensure_reference_prices(
    needed: set[tuple[int, date]],
) -> dict[tuple[int, date], float]:
    """Return prices for every (type_id, day) in `needed`, fetching+caching misses.

    Uses The Forge daily average via Goonmetrics (ESI for types Goonmetrics
    does not track). Writes new rows to ledger_reference_prices once; never
    overwrites (deterministic recompute).
    """
    if not needed:
        return {}
    type_ids = sorted({tid for tid, _d in needed})
    cached = db.list_cached_reference_prices(type_ids)
    out: dict[tuple[int, date], float] = {}
    missing_types: set[int] = set()
    for tid, day in needed:
        hit = cached.get((tid, day))
        if hit is None:
            # price_date from Postgres may be date already; also try iso.
            missing_types.add(tid)
        else:
            out[(tid, day)] = float(hit[0])

    # Re-check with normalised dates from cache keys.
    if missing_types:
        # Some cache hits may have been missed due to date type mismatch.
        for (tid, pd), (price, _src) in cached.items():
            d = _as_date(pd)
            if (tid, d) in needed and (tid, d) not in out:
                out[(tid, d)] = float(price)
        missing_types = {tid for tid, d in needed if (tid, d) not in out}

    if not missing_types:
        return out

    client = GoonmetricsClient()
    # Fetch full history windows; pick the day we need. esi_for_missing =
    # every type so blueprints etc. still get a price when Goonmetrics
    # omits them.
    points = client.price_history_chunked(
        _REFERENCE_REGION_ID,
        sorted(missing_types),
        esi_for_missing=set(missing_types),
    )
    by_type_day: dict[tuple[int, date], tuple[float, str]] = {}
    for p in points:
        avg = float(p.avg_price or 0.0)
        # Untraded days (avgPrice 0) are not a usable reference (same rule as
        # history_backtest: zero averages are not real prices).
        if avg <= 0.0:
            continue
        d = _as_date(p.date)
        # Prefer Goonmetrics when both exist; first write wins via upsert DO NOTHING.
        key = (int(p.type_id), d)
        if key not in by_type_day:
            # HistoryPoint has no source field; Goonmetrics is the primary path.
            by_type_day[key] = (avg, "goonmetrics_or_esi")

    to_insert: list[tuple] = []
    for tid, day in needed:
        if (tid, day) in out:
            continue
        hit = by_type_day.get((tid, day))
        if hit is None:
            continue
        price, source = hit
        out[(tid, day)] = price
        to_insert.append((tid, day, price, source))
    if to_insert:
        db.upsert_reference_prices(to_insert)
    return out


def _collect_price_needs(
    t0: datetime,
    txs: list[WalletTransaction],
    jobs: list[IndustryJob],
    stock_types: set[int],
) -> set[tuple[int, date]]:
    """(type_id, day) pairs the engine may look up (opening, unknown, shortfall)."""
    needs: set[tuple[int, date]] = set()
    t0_day = _as_utc(t0).date()
    for tid in stock_types:
        needs.add((tid, t0_day))
    for tx in txs:
        if _as_utc(tx.date) < _as_utc(t0):
            continue
        day = _as_utc(tx.date).date()
        needs.add((int(tx.type_id), day))
        needs.add((int(tx.type_id), t0_day))
    for job in jobs:
        if job.start_date is None:
            continue
        if _as_utc(job.start_date) < _as_utc(t0):
            continue
        day = _as_utc(job.start_date).date()
        for inp in job.inputs:
            needs.add((int(inp.type_id), day))
            needs.add((int(inp.type_id), t0_day))
        if job.blueprint_type_id:
            needs.add((int(job.blueprint_type_id), day))
        if job.product_type_id:
            needs.add((int(job.product_type_id), t0_day))
    return needs


def _esi_stock_excluding_blueprints(type_ids: set[int]) -> dict[int, float]:
    """Tenant-wide ESI stock (assembled/fitted already excluded), no blueprints."""
    if not type_ids:
        return {}
    blueprints = _blueprint_type_ids(type_ids)
    item_ids = [t for t in type_ids if t not in blueprints]
    if not item_ids:
        return {}
    # location_id=None -> all locations (D2: one inventory per type).
    return storage.esi_stock_at_location_bulk(item_ids, location_id=None)


def load_ledger_snapshot() -> Optional[LedgerSnapshot]:
    """Assemble a LedgerSnapshot from the archive, or None when T0 is unknown."""
    t0 = access.cutover_t0_for_production()
    if t0 is None:
        return None
    t0 = _as_utc(t0)

    tx_rows = access.list_all_wallet_transactions_for_production()
    journal_rows = access.list_all_wallet_journal_for_production()
    order_rows = access.list_all_market_orders_for_production()
    job_rows = access.list_all_industry_jobs_for_production()
    input_rows = access.list_all_job_inputs_for_production()

    inputs_by_job: dict[tuple, list[JobInput]] = defaultdict(list)
    for ot, oid, job_id, type_id, qty, source in input_rows:
        inputs_by_job[(ot, int(oid), int(job_id))].append(
            JobInput(type_id=int(type_id), quantity=float(qty), source=str(source)),
        )

    txs = [
        WalletTransaction(
            owner_type=r[0], owner_id=int(r[1]), division=int(r[2]),
            transaction_id=int(r[3]), date=_as_utc(r[4]), type_id=int(r[5]),
            location_id=int(r[6]), unit_price=float(r[7]), quantity=float(r[8]),
            is_buy=bool(r[9]), journal_ref_id=int(r[10]) if r[10] is not None else None,
            region_id=int(r[11]) if r[11] is not None else None,
            freight_rate_per_m3=float(r[12]) if r[12] is not None else None,
            freight_m3=float(r[13]) if r[13] is not None else None,
        )
        for r in tx_rows
    ]
    journals = [
        JournalEntry(
            owner_type=r[0], owner_id=int(r[1]), division=int(r[2]),
            journal_id=int(r[3]), date=_as_utc(r[4]), ref_type=str(r[5]),
            amount=float(r[6]),
            context_id=int(r[7]) if r[7] is not None else None,
            context_id_type=str(r[8]) if r[8] is not None else None,
        )
        for r in journal_rows
    ]
    orders = [
        MarketOrder(
            owner_type=r[0], owner_id=int(r[1]), order_id=int(r[2]),
            type_id=int(r[3]), location_id=int(r[4]), is_buy_order=bool(r[5]),
            price=float(r[6]),
            volume_total=float(r[7]) if r[7] is not None else 0.0,
            issued=_as_utc(r[8]) if r[8] is not None else None,
            state=str(r[9]) if r[9] is not None else None,
        )
        for r in order_rows
    ]
    jobs: list[IndustryJob] = []
    for r in job_rows:
        key = (r[0], int(r[1]), int(r[2]))
        jobs.append(IndustryJob(
            owner_type=r[0], owner_id=int(r[1]), job_id=int(r[2]),
            activity_id=int(r[3]), blueprint_type_id=int(r[5]),
            product_type_id=int(r[6]) if r[6] is not None else None,
            runs=int(r[7] or 0),
            licensed_runs=int(r[8]) if r[8] is not None else None,
            successful_runs=int(r[9]) if r[9] is not None else None,
            cost=float(r[10]) if r[10] is not None else None,
            installer_id=int(r[12]) if r[12] is not None else None,
            start_date=_as_utc(r[13]) if r[13] is not None else None,
            end_date=_as_utc(r[14]) if r[14] is not None else None,
            completed_date=_as_utc(r[15]) if r[15] is not None else None,
            status=str(r[16]) if r[16] is not None else None,
            probability=float(r[17]) if r[17] is not None else None,
            inputs=tuple(inputs_by_job.get(key, ())),
            output_quantity=_output_quantity(r),
        ))

    production_types = _production_type_ids(job_rows)
    type_ids: set[int] = set(production_types)
    for tx in txs:
        type_ids.add(int(tx.type_id))
    for job in jobs:
        type_ids.add(int(job.blueprint_type_id))
        if job.product_type_id is not None:
            type_ids.add(int(job.product_type_id))
        for inp in job.inputs:
            type_ids.add(int(inp.type_id))

    stock = _esi_stock_excluding_blueprints(type_ids)
    type_ids.update(stock.keys())

    price_needs = _collect_price_needs(t0, txs, jobs, set(stock.keys()) | set(production_types))
    ref_prices = _ensure_reference_prices(price_needs)

    return LedgerSnapshot(
        t0=t0,
        reference_prices=ref_prices,
        esi_stock_by_type={int(k): float(v) for k, v in stock.items()},
        production_type_ids=production_types,
        tenant_character_ids=_tenant_character_ids(),
        wallet_transactions=tuple(txs),
        journal_entries=tuple(journals),
        market_orders=tuple(orders),
        industry_jobs=tuple(jobs),
        opening_cost_overrides=db.list_opening_cost_overrides(),
        home_structure_id=TRADING_CONFIG.structure_id,
        bpc_run_reference_prices={},  # BPC-run unknowns stay 0 + warning (plan: no guess)
        relic_type_ids=_relic_type_ids(),
        # BPO vs BPC share a type_id; wallet txs cannot tell them apart.
        # Leave empty so buys become layers; capital_in_bpos still gets research.
        bpo_type_ids=frozenset(),
    )


def _layer_row(layer) -> tuple:
    c = layer.unit_components
    return (
        int(layer.type_id), str(layer.unit), float(layer.quantity_left),
        float(layer.unit_cost), str(layer.origin), str(layer.source_ref),
        float(c.materials_bought), float(c.materials_estimated), float(c.freight),
        float(c.job), float(c.invention), float(c.fees),
        layer.layer_date, layer.job_id,
    )


def _result_row(result) -> tuple:
    return (
        str(result.sale_ref), int(result.type_id), float(result.quantity),
        float(result.revenue), float(result.cost), float(result.profit),
        float(result.coverage_share), result.sale_date,
    )


def rebuild_realized() -> dict:
    """Load ledger → pure costing → replace derived tables.

    Returns a summary dict (counts, t0, run_id, warnings). Does not raise
    ActionError - callers in Phase 3 wrap that. An empty archive (no T0)
    clears derived tables and records a run with a warning.
    """
    snapshot = load_ledger_snapshot()
    if snapshot is None:
        warnings = ["no wallet archive yet; cutover T0 unknown"]
        run_id = db.replace_derived_costing(
            layers=[], results=[], cutover_t0=None, warnings=warnings,
        )
        return {
            "run_id": run_id,
            "cutover_t0": None,
            "layers": 0,
            "realized_results": 0,
            "warnings": warnings,
        }

    result = rebuild_costing(snapshot)
    # Persist remaining layers (quantity_left > 0) and all realized sales.
    layer_rows = [_layer_row(L) for L in result.layers]
    result_rows = [_result_row(r) for r in result.realized_results]
    extra_warnings = list(result.warnings)
    if result.fees_without_sale_total:
        extra_warnings.append(
            f"fees_without_sale={result.fees_without_sale_total:.2f}"
        )
    if result.fees_on_other_trading:
        extra_warnings.append(
            f"fees_on_other_trading={result.fees_on_other_trading:.2f}"
        )
    run_id = db.replace_derived_costing(
        layers=layer_rows,
        results=result_rows,
        cutover_t0=snapshot.t0,
        warnings=extra_warnings,
    )
    return {
        "run_id": run_id,
        "cutover_t0": snapshot.t0.isoformat(),
        "layers": len(layer_rows),
        "realized_results": len(result_rows),
        "jobs": len(result.jobs),
        "cancelled_losses": len(result.cancelled_job_losses),
        "coverage": result.coverage(),
        "warnings": extra_warnings,
        "wallet_transactions": len(snapshot.wallet_transactions),
        "industry_jobs": len(snapshot.industry_jobs),
        "journal_entries": len(snapshot.journal_entries),
    }

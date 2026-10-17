"""Thin I/O layer around pure `costing.rebuild_costing`.

Reads the archive through `access.py` (D11), fills and caches reference
prices in `ledger_reference_prices` (D4/D5/D8), builds a `LedgerSnapshot`,
runs the pure engine, and replaces the derived tables. No business logic
beyond assembling inputs the plan already names.

Expected scale (plan Phase 0 / lessons): the C-J seller alone can have
~12,000 wallet transactions / 30 days and ~10,000 journal entries / week.
All archive reads here are one query per table for the shared-owner set
(not per-row round trips).

Reference prices are fetched in two passes: costing runs once with what is
cached, reports exactly the (type, day) lookups that missed
(`CostingResult.price_requests`), the loader fetches only those and runs
again. Goonmetrics covers only ~28 days (live 2026-10-17: 09-18..10-15 on
10-17), so days it lacks come from ESI's daily history (~400 days), else the
nearest traded day within `_PRICE_NEAREST_DAYS`. A day nothing can price is
cached as a miss (source "none") once it is older than
`_NEGATIVE_CACHE_MIN_AGE_DAYS`, so a rebuild never asks again.
"""
from __future__ import annotations

import dataclasses
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ... import storage
from ...config import TRADING_CONFIG
from ...esi_client import ESIClient, ESIError
from ...goonmetrics_client import GoonmetricsClient
from ..config import PRODUCTION_CONFIG
from ..constants import (
    ACTIVITY_INVENTION,
    ACTIVITY_MANUFACTURING,
    ACTIVITY_REACTION,
    DECRYPTORS,
)
from . import access, db
from .costing import (
    UNIT_ITEM,
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

# A day without trades takes the nearest traded day, earlier first.
_PRICE_NEAREST_DAYS = 7
# Today's and yesterday's averages may not be published yet - never cache
# a miss for them.
_NEGATIVE_CACHE_MIN_AGE_DAYS = 2
_MISSING_SOURCE = "none"

_DECRYPTOR_RUN_BONUS = {d.type_id: d.run_bonus for d in DECRYPTORS.values() if d.type_id}


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
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT t.type_id FROM sde_types t JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE t.type_id = ANY(?) AND g.category_id = ?",
            (list(type_ids), _BLUEPRINT_CATEGORY_ID),
        ).fetchall()
    return {int(r[0]) for r in rows}


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
            "JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE p.activity_id = ? AND g.category_id <> ?",
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


def _output_runs_per_success(job_row: tuple, inputs: list[JobInput]) -> Optional[float]:
    """Invention: recipe base runs + the frozen decryptor's run bonus. ESI's
    licensed_runs on an invention job is not this (Phase 2 review)."""
    if int(job_row[3]) != ACTIVITY_INVENTION or job_row[6] is None:
        return None
    recipe = storage.get_invention_recipe(int(job_row[5]), int(job_row[6]))
    if not recipe or not recipe.get("base_runs"):
        return None
    bonus = next((_DECRYPTOR_RUN_BONUS[i.type_id] for i in inputs if i.type_id in _DECRYPTOR_RUN_BONUS), 0)
    return float(recipe["base_runs"]) + float(bonus)


def _uses_bpc(job_row: tuple, bp_runs: dict[int, int], invented: frozenset[int]) -> Optional[bool]:
    """Manufacturing/reaction only: did the job run on a BPC? From the
    archived blueprint (runs -1 = BPO); a T2/T3 blueprint is always a copy."""
    activity_id = int(job_row[3])
    if activity_id == 9:
        activity_id = ACTIVITY_REACTION
    if activity_id not in (ACTIVITY_MANUFACTURING, ACTIVITY_REACTION):
        return None
    blueprint_id = job_row[4]
    if blueprint_id is not None and int(blueprint_id) in bp_runs:
        return bp_runs[int(blueprint_id)] != -1
    if int(job_row[5]) in invented:
        return True
    return None


# ------------------------------------------------------------ reference prices
def _cached_prices(type_ids: set[int]) -> tuple[dict[tuple[int, date], float], set[tuple[int, date]]]:
    """(prices, known misses) from ledger_reference_prices."""
    prices: dict[tuple[int, date], float] = {}
    misses: set[tuple[int, date]] = set()
    for (tid, pd), (price, source) in db.list_cached_reference_prices(sorted(type_ids)).items():
        key = (int(tid), _as_date(pd))
        if source == _MISSING_SOURCE:
            misses.add(key)
        else:
            prices[key] = float(price)
    return prices, misses


def _pick_price(series: dict[date, tuple[float, str]], day: date) -> Optional[tuple[float, str]]:
    """Exact day, else the nearest traded day (earlier first) within
    _PRICE_NEAREST_DAYS. Deterministic for a given series."""
    if day in series:
        return series[day]
    for offset in range(1, _PRICE_NEAREST_DAYS + 1):
        for candidate, label in ((day - timedelta(days=offset), "prior_day"),
                                 (day + timedelta(days=offset), "next_day")):
            hit = series.get(candidate)
            if hit is not None:
                return hit[0], f"{hit[1]}_{label}"
    return None


def _esi_history(type_ids: list[int]) -> dict[int, dict[date, tuple[float, str]]]:
    client = ESIClient()

    def one(tid: int):
        try:
            return tid, client.region_market_history(_REFERENCE_REGION_ID, tid)
        except ESIError:
            return tid, []

    out: dict[int, dict[date, tuple[float, str]]] = defaultdict(dict)
    if not type_ids:
        return out
    with ThreadPoolExecutor(max_workers=min(6, len(type_ids))) as pool:
        for tid, rows in pool.map(storage.with_current_tenant(one), type_ids):
            for row in rows or []:
                avg = float(row.get("average") or 0.0)
                if avg > 0:
                    out[int(tid)][_as_date(row["date"])] = (avg, "esi")
    return out


def _fetch_reference_prices(requests: set[tuple[int, date]], today: date) -> dict[tuple[int, date], float]:
    """Price exactly the requested (type, day) pairs, cache the results (and
    old misses), return the prices found."""
    if not requests:
        return {}
    types = sorted({tid for tid, _d in requests})
    series: dict[int, dict[date, tuple[float, str]]] = defaultdict(dict)
    for p in GoonmetricsClient().price_history_chunked(_REFERENCE_REGION_ID, types):
        avg = float(p.avg_price or 0.0)
        # Untraded days (avgPrice 0) are not a price (history_backtest rule).
        if avg > 0:
            series[int(p.type_id)][_as_date(p.date)] = (avg, "goonmetrics")

    found: dict[tuple[int, date], tuple[float, str]] = {}
    for tid, day in requests:
        hit = _pick_price(series[tid], day)
        if hit is not None:
            found[(tid, day)] = hit
    esi_types = sorted({tid for tid, day in requests if (tid, day) not in found})
    for tid, points in _esi_history(esi_types).items():
        for day_, point in points.items():
            series[tid].setdefault(day_, point)
    for tid, day in requests:
        if (tid, day) not in found:
            hit = _pick_price(series[tid], day)
            if hit is not None:
                found[(tid, day)] = hit

    rows = [(tid, day, price, source) for (tid, day), (price, source) in sorted(found.items())]
    cutoff = today - timedelta(days=_NEGATIVE_CACHE_MIN_AGE_DAYS)
    rows += [(tid, day, 0.0, _MISSING_SOURCE) for tid, day in sorted(requests)
             if (tid, day) not in found and day <= cutoff]
    db.upsert_reference_prices(rows)
    return {key: price for key, (price, _source) in found.items()}


# ------------------------------------------------------------------- stock
def _opening_stock(type_ids: set[int]) -> dict[int, float]:
    """Items the tenant holds today, for the opening inventory (D8): assets
    shared with Production (assembled ships/fitting excluded by the stock
    reader, blueprints excluded), plus quantities still listed in open sell
    orders and outstanding contracts - they left the hangar, not the
    inventory."""
    if not type_ids:
        return {}
    blueprints = _blueprint_type_ids(type_ids)
    item_ids = [t for t in type_ids if t not in blueprints]
    if not item_ids:
        return {}
    asset_owners = access.shared_owners("assets")
    stock = storage.esi_stock_at_location_bulk(
        item_ids, location_id=None,
        owner_character_ids=[oid for ot, oid in asset_owners if ot == "character"],
        owner_corporation_ids=[oid for ot, oid in asset_owners if ot == "corporation"],
    )
    totals = defaultdict(float, {int(k): float(v) for k, v in stock.items()})
    order_owners = access.shared_owners("market_orders")
    listed = db.listed_sell_qty_by_type(
        [oid for ot, oid in order_owners if ot == "character"],
        [oid for ot, oid in order_owners if ot == "corporation"],
    )
    in_contracts = db.outstanding_contract_qty_by_type(access.shared_owners("contracts"))
    for extra in (listed, in_contracts):
        for tid, qty in extra.items():
            if tid in blueprints:
                continue
            totals[int(tid)] += float(qty)
    return {tid: qty for tid, qty in totals.items() if qty > 0}


# ------------------------------------------------------------------ snapshot
def load_ledger_snapshot() -> Optional[LedgerSnapshot]:
    """Assemble a LedgerSnapshot from the archive (with cached reference
    prices only), or None when T0 is unknown."""
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

    bp_runs = db.blueprint_runs_by_item(
        access.shared_owners("blueprints"),
        [int(r[4]) for r in job_rows if r[4] is not None],
    )
    invented = db.invented_blueprint_type_ids()
    jobs: list[IndustryJob] = []
    for r in job_rows:
        key = (r[0], int(r[1]), int(r[2]))
        job_inputs = list(inputs_by_job.get(key, ()))
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
            inputs=tuple(job_inputs),
            output_quantity=_output_quantity(r),
            output_runs_per_success=_output_runs_per_success(r, job_inputs),
            uses_bpc=_uses_bpc(r, bp_runs, invented),
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

    stock = _opening_stock(type_ids)
    type_ids.update(stock.keys())
    prices, _misses = _cached_prices(type_ids)
    # D3: the market only trades blueprint originals, so a blueprint bought
    # there is a BPO (capital), never an inventory layer.
    bpo_types = frozenset(_blueprint_type_ids({int(tx.type_id) for tx in txs if tx.is_buy}))

    return LedgerSnapshot(
        t0=t0,
        reference_prices=prices,
        esi_stock_by_type={int(k): float(v) for k, v in stock.items()},
        production_type_ids=production_types,
        tenant_character_ids=_tenant_character_ids(),
        wallet_transactions=tuple(txs),
        journal_entries=tuple(journals),
        market_orders=tuple(orders),
        industry_jobs=tuple(jobs),
        opening_cost_overrides=db.list_opening_cost_overrides(),
        home_structure_id=PRODUCTION_CONFIG.home_location_id or TRADING_CONFIG.structure_id,
        bpc_run_reference_prices={},  # no source for a per-run BPC price: missing + flagged
        relic_type_ids=_relic_type_ids(),
        bpo_type_ids=bpo_types,
    )


def _run_costing(snapshot: LedgerSnapshot, today: Optional[date] = None):
    """Run costing; fetch exactly the item prices it missed (minus cached
    misses) and run once more if anything new was found."""
    result = rebuild_costing(snapshot)
    requests = {(tid, day) for tid, unit, day in result.price_requests if unit == UNIT_ITEM}
    if not requests:
        return result
    _prices, misses = _cached_prices({tid for tid, _d in requests})
    requests -= misses
    found = _fetch_reference_prices(requests, today or datetime.now(timezone.utc).date())
    if not found:
        return result
    merged = {**snapshot.reference_prices, **found}
    return rebuild_costing(dataclasses.replace(snapshot, reference_prices=merged))


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

    result = _run_costing(snapshot)
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
        "estimated_results": sum(1 for r in result.realized_results if r.estimated),
        "warnings": extra_warnings,
        "wallet_transactions": len(snapshot.wallet_transactions),
        "industry_jobs": len(snapshot.industry_jobs),
        "journal_entries": len(snapshot.journal_entries),
    }

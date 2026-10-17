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

from .. import storage
from ..config import TRADING_CONFIG
from ..esi_client import ESIClient, ESIError
from ..goonmetrics_client import GoonmetricsClient
from ..doctrine.config import DOCTRINE_CONFIG
from ..production.config import PRODUCTION_CONFIG
from ..station_trading.config import STATION_TRADING_CONFIG
from ..production.constants import (
    ACTIVITY_INVENTION,
    ACTIVITY_MANUFACTURING,
    ACTIVITY_REACTION,
    DECRYPTORS,
)
from . import access, db
from .costing import (
    ORIGIN_BOUGHT,
    UNIT_ITEM,
    ContractItem,
    IndustryJob,
    ItemExchangeContract,
    JobInput,
    JournalEntry,
    LedgerSnapshot,
    MarketOrder,
    WalletTransaction,
    rebuild_costing,
)

log = logging.getLogger(__name__)

# Bump when a rebuild must re-derive opening inventory once. 2: Phase 5
# contract flows. 3: stock without a purchase is valued at the planner's
# price instead of The Forge daily average. 4: sale channels, station pool
# and hub freight (unified plan Phase 2). NULL counts as older.
COSTING_VERSION = 4

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
_VALUATION_UNAVAILABLE = "unavailable"

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
    """The tenant's own characters: the registry entry (the login character)
    plus every character that holds a token in this tenant. The registry
    alone has one character per tenant, so a tenant's other characters
    would read as outsiders (their corp jobs skipped, contracts between
    them booked as sales)."""
    tenant_id = storage.get_current_tenant()
    entries = storage.list_tenant_registry_entries(str(tenant_id))
    ids = {int(eid) for et, eid in entries if et == "character"}
    from ..auth import TokenManager
    from ..config import OAUTH_CONFIG
    ids.update(int(rec.character_id) for rec in TokenManager(OAUTH_CONFIG).list_records()
               if rec.character_id is not None)
    return frozenset(ids)


def _tenant_corporation_ids() -> frozenset[int]:
    """Corps this tenant owns: each character's registered corporation, plus
    corporation owners shared for the ledger kinds. A personal contract's
    issuer_corporation_id is not enough on its own (that is the issuer's
    corp, including corpmates). Report tests that never create the registry
    table get an empty registry half and still see shared corp owners."""
    import psycopg

    ids: set[int] = set()
    try:
        tenant_id = storage.get_current_tenant()
        with storage.connect_unscoped() as conn:
            rows = conn.execute(
                "SELECT corporation_id FROM tenant_registry_entries "
                "WHERE tenant_id = ? AND corporation_id IS NOT NULL",
                (str(tenant_id),),
            ).fetchall()
        ids.update(int(r[0]) for r in rows if r[0] is not None)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        pass
    for kind in ("contracts", "assets", "industry_jobs", "wallet"):
        for owner_type, owner_id in access.shared_owners(kind):
            if owner_type == "corporation":
                ids.add(int(owner_id))
    return frozenset(ids)


def _opening_reset_for_version(explicit: bool) -> bool:
    """True when this rebuild must re-derive opening quantities. An explicit
    reset always does. Otherwise a missing run does not, and a run whose
    costing_version is NULL or older than COSTING_VERSION does exactly once."""
    if explicit:
        return True
    row = db.latest_costing_version()
    if row is None:
        return False
    version = row[1]
    return version is None or int(version) < COSTING_VERSION


def _agreed_blueprint_runs(values: list[int]) -> Optional[int]:
    found = {int(v) for v in values}
    if len(found) == 1:
        return next(iter(found))
    return None


def _freeze_contract_freight(enricher, start_location, type_id: int, quantity: float):
    """(rate, m3, frozen). An unresolved start location stays unfrozen."""
    if start_location is None:
        return None, None, True
    region = enricher.region(int(start_location))
    if region is None:
        return None, None, False
    rate, m3 = enricher.freight(region, int(start_location), int(type_id), quantity, True)
    return rate, m3, True


def _doctrine_fitting_ids(contract_ids: list[int]) -> dict[int, str]:
    """fitting_id from doctrine_contract_history, when that table exists."""
    if not contract_ids:
        return {}
    import psycopg

    try:
        with storage.connect() as conn:
            rows = conn.execute(
                "SELECT contract_id, fitting_id FROM doctrine_contract_history "
                "WHERE contract_id = ANY(?)",
                (list(contract_ids),),
            ).fetchall()
    except psycopg.errors.UndefinedTable:
        return {}
    return {int(row[0]): str(row[1]) for row in rows if row[1]}


def _load_contracts() -> tuple:
    """Finished item-exchange contracts shared with any realized tool, deduped
    by contract id. Freight is frozen onto item rows the first time the start
    location's region can be resolved. Courier rows are not returned.
    `is_doctrine` is U7: doctrine structure, issuer is a tenant character,
    archive owner shares contracts with Doctrine."""
    owners = access.shared_owners("contracts")
    if not owners:
        return ()
    headers = db.list_contracts_for_owners(owners)
    item_rows = db.list_contract_items_for_owners(owners)
    items_by_key: dict[tuple, list] = defaultdict(list)
    for row in item_rows:
        items_by_key[(row[0], int(row[1]), int(row[2]))].append(row)
    blueprint_types = _blueprint_type_ids({int(row[4]) for row in item_rows})
    runs_by_type = db.blueprint_runs_by_type(
        access.shared_owners("blueprints"), sorted(blueprint_types),
    )
    from . import ingest
    enricher = ingest._TxnEnricher(PRODUCTION_CONFIG)
    freight_updates: list[tuple] = []
    region_updates: list[tuple] = []
    grouped: dict[int, list] = defaultdict(list)
    for row in headers:
        grouped[int(row[2])].append(row)
        if row[15] is None and row[6] is not None:
            region = enricher.region(int(row[6]))
            if region is not None:
                region_updates.append((int(region), row[0], int(row[1]), int(row[2])))
    if region_updates:
        db.update_contract_regions(region_updates)

    doc_owners = set(access.shared_owners_for("contracts", "doctrine"))
    doc_structure = DOCTRINE_CONFIG.effective_structure_id
    tenant_chars = _tenant_character_ids()
    fittings = _doctrine_fitting_ids(list(grouped))
    contracts = []
    for contract_id, copies in grouped.items():
        def _nitems(copy, cid=contract_id):
            return len(items_by_key.get((copy[0], int(copy[1]), cid), ()))

        copies.sort(key=lambda copy: (_nitems(copy) == 0, copy[0] != "corporation"))
        chosen = copies[0]
        if str(chosen[8] or "") != "item_exchange":
            continue
        if str(chosen[9] or "").lower() != "finished" or chosen[12] is None:
            continue
        item_copy = max(copies, key=_nitems)
        corp_issued = any(
            copy[0] == "corporation" and copy[4] is not None and int(copy[4]) == int(copy[1])
            for copy in copies
        )
        built = []
        for item in items_by_key.get((item_copy[0], int(item_copy[1]), contract_id), ()):
            type_id = int(item[4])
            quantity = float(item[5])
            is_blueprint = type_id in blueprint_types
            rate, m3, frozen = item[8], item[9], bool(item[10])
            if not frozen:
                rate, m3, frozen = _freeze_contract_freight(enricher, chosen[6], type_id, quantity)
                if frozen:
                    freight_updates.append((
                        rate, m3, True, item[0], int(item[1]), int(item[2]), int(item[3]),
                    ))
            built.append(ContractItem(
                record_id=int(item[3]), type_id=type_id, quantity=quantity,
                is_included=bool(item[6]), is_singleton=bool(item[7]),
                is_blueprint=is_blueprint,
                blueprint_runs=_agreed_blueprint_runs(runs_by_type.get(type_id, [])) if is_blueprint else None,
                freight_rate_per_m3=None if rate is None else float(rate),
                freight_m3=None if m3 is None else float(m3),
            ))
        issuer_id = None if chosen[3] is None else int(chosen[3])
        start_location_id = None if chosen[6] is None else int(chosen[6])
        is_doctrine = (
            doc_structure is not None
            and (chosen[0], int(chosen[1])) in doc_owners
            and start_location_id is not None
            and int(start_location_id) == int(doc_structure)
            and issuer_id is not None
            and issuer_id in tenant_chars
        )
        contracts.append(ItemExchangeContract(
            contract_id=contract_id, owner_type=chosen[0], owner_id=int(chosen[1]),
            date_completed=_as_utc(chosen[12]),
            issuer_id=issuer_id,
            issuer_corporation_id=None if chosen[4] is None else int(chosen[4]),
            acceptor_id=None if chosen[5] is None else int(chosen[5]),
            price=float(chosen[10] or 0), reward=float(chosen[11] or 0),
            start_location_id=start_location_id,
            end_location_id=None if chosen[7] is None else int(chosen[7]),
            items=tuple(built), corp_issued=corp_issued,
            status=str(chosen[9] or ""), contract_type=str(chosen[8] or "item_exchange"),
            is_doctrine=is_doctrine,
            fitting_id=fittings.get(contract_id) if is_doctrine else None,
        ))
    if freight_updates:
        db.update_contract_item_freight(freight_updates)
    return tuple(contracts)


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
    """(prices, known misses) for stock without a purchase (D5/D8).

    A (type, day) is valued at the frozen planner valuation
    (ledger_valuation_prices: the cheaper of the buy-list purchase price and
    the build cost). Only where the planner had no value ("unavailable")
    does The Forge daily average (ledger_reference_prices) apply. A known
    miss is a key neither source can price. A key with no valuation row yet
    is neither: the next rebuild values it."""
    ids = sorted(type_ids)
    forge: dict[tuple[int, date], tuple[float, str]] = {
        (int(tid), _as_date(pd)): hit for (tid, pd), hit in db.list_cached_reference_prices(ids).items()
    }
    prices: dict[tuple[int, date], float] = {}
    misses: set[tuple[int, date]] = set()
    for (tid, pd), (value, source) in db.list_cached_valuation_prices(ids).items():
        key = (int(tid), _as_date(pd))
        if source != _VALUATION_UNAVAILABLE and value is not None:
            prices[key] = float(value)
            continue
        hit = forge.get(key)
        if hit is None:
            continue
        if hit[1] == _MISSING_SOURCE:
            misses.add(key)
        else:
            prices[key] = float(hit[0])
    return prices, misses


def _planner_values(type_ids: set[int]) -> dict[int, tuple[float, str]]:
    from ..production.engine import planner_unit_values
    return planner_unit_values(type_ids, PRODUCTION_CONFIG)


def _fetch_valuations(requests: set[tuple[int, date]]) -> dict[tuple[int, date], float]:
    """Value the requested (type, day) pairs that have no valuation row yet
    with today's planner prices and freeze them. The home structure's market
    has no price history, so a day already past is valued at the price of
    the rebuild that first needs it. A type the planner cannot price is
    stored as unavailable and falls back to The Forge daily average. If the
    planner priced nothing at all (an ESI outage), nothing is stored and the
    next rebuild tries again."""
    if not requests:
        return {}
    known = {(int(tid), _as_date(pd)) for (tid, pd) in
             db.list_cached_valuation_prices(sorted({tid for tid, _d in requests}))}
    todo = sorted(key for key in requests if key not in known)
    if not todo:
        return {}
    try:
        values = _planner_values({tid for tid, _d in todo})
    except Exception:  # noqa: BLE001 - a valuation failure must not fail the rebuild
        log.exception("realized: planner valuation failed; using The Forge daily average")
        return {}
    if not values:
        return {}
    rows: list[tuple] = []
    found: dict[tuple[int, date], float] = {}
    for tid, day in todo:
        hit = values.get(tid)
        if hit is None:
            rows.append((tid, day, None, _VALUATION_UNAVAILABLE))
        else:
            rows.append((tid, day, float(hit[0]), str(hit[1])))
            found[(tid, day)] = float(hit[0])
    db.upsert_valuation_prices(rows)
    return found


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
def _frozen_opening(t0: datetime, scope_hash: str, reset: bool) -> Optional[dict[int, float]]:
    """Opening quantities frozen by earlier rebuilds, or None when they must
    be recomputed: nothing frozen yet, an explicit reset, or T0 / the
    shared-owner fingerprint changed since they were frozen."""
    if reset:
        return None
    rows = db.list_frozen_opening()
    if not rows:
        return None
    for _tid, _raw, frozen_t0, frozen_hash, _at in rows:
        if _as_utc(frozen_t0) != t0 or frozen_hash != scope_hash:
            return None
    return {int(r[0]): float(r[1]) for r in rows}


def load_ledger_snapshot(*, scope_hash: Optional[str] = None,
                         reset_opening: bool = False) -> Optional[LedgerSnapshot]:
    """Assemble a LedgerSnapshot from the archive (with cached reference
    prices only), or None when T0 is unknown."""
    t0 = access.cutover_t0_for_production()
    if t0 is None:
        return None
    t0 = _as_utc(t0)
    if scope_hash is None:
        scope_hash = access.owner_scope_hash()

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
            facility_id=int(r[11]) if r[11] is not None else None,
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

    contracts = _load_contracts()
    production_types = _production_type_ids(job_rows)
    type_ids: set[int] = set(production_types)
    for contract in contracts:
        for item in contract.items:
            if not item.is_blueprint:
                type_ids.add(int(item.type_id))
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
        frozen_opening=_frozen_opening(t0, scope_hash, reset_opening),
        contracts=contracts,
        tenant_corporation_ids=_tenant_corporation_ids(),
        station_trading_station_id=STATION_TRADING_CONFIG.station_id,
        station_trading_wallet_owners=frozenset(
            access.shared_owners_for("wallet", "station_trading"),
        ),
        doctrine_structure_id=DOCTRINE_CONFIG.effective_structure_id,
        doctrine_contract_owners=frozenset(
            access.shared_owners_for("contracts", "doctrine"),
        ),
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
    found = _fetch_valuations(requests)
    rest = requests - set(found)
    found.update(_fetch_reference_prices(rest, today or datetime.now(timezone.utc).date()))
    if not found:
        return result
    merged = {**snapshot.reference_prices, **found}
    return rebuild_costing(dataclasses.replace(snapshot, reference_prices=merged))


def _components(c) -> tuple:
    return (
        float(c.materials_bought), float(c.materials_estimated), float(c.freight),
        float(c.job), float(c.invention), float(c.fees),
    )


def _layer_row(layer) -> tuple:
    return (
        int(layer.type_id), str(layer.unit), float(layer.quantity_left),
        float(layer.unit_cost), str(layer.origin), str(layer.source_ref),
        *_components(layer.unit_components),
        layer.layer_date, layer.job_id,
        float(layer.quantity), bool(layer.assumed_me), bool(layer.assumed_decryptor),
        bool(layer.estimated_origin), bool(layer.price_missing), int(layer.layer_id),
        None if layer.source is None else str(layer.source),
        str(layer.pool or "global"),
    )


def _result_row(result) -> tuple:
    return (
        str(result.sale_ref), int(result.type_id), float(result.quantity),
        float(result.revenue), float(result.cost), float(result.profit),
        float(result.coverage_share), result.sale_date,
        float(result.gross),
        None if result.sales_tax is None else float(result.sales_tax),
        float(result.broker_fee),
        int(result.location_id),
        bool(result.price_missing), bool(result.estimated), list(result.warnings),
        *_components(result.components),
        str(result.source or "market"),
        str(result.channel or "production"),
        result.owner_type,
        None if result.owner_id is None else int(result.owner_id),
        result.fitting_id,
        None if result.issuer_id is None else int(result.issuer_id),
    )


def _draw_rows(result, snapshot) -> list[tuple]:
    layers = {int(layer.layer_id): layer for layer in result.all_layers}
    txs = {tx.ref: tx for tx in snapshot.wallet_transactions}
    contracts = {int(contract.contract_id): contract for contract in snapshot.contracts}

    def one(kind: str, ref: str, seq: int, draw, channel) -> tuple:
        layer = layers.get(int(draw.layer_id))
        tx = txs.get(layer.source_ref) if layer is not None else None
        qty = float(draw.quantity)
        location_id = None
        unit_price = None
        rate = None
        if layer is not None and layer.origin == ORIGIN_BOUGHT and tx is not None:
            location_id = int(tx.location_id)
            unit_price = float(tx.unit_price)
            if tx.freight_rate_per_m3 is not None:
                rate = float(tx.freight_rate_per_m3)
        elif layer is not None and layer.origin == ORIGIN_BOUGHT and str(layer.source_ref).startswith("contract:"):
            contract = contracts.get(int(str(layer.source_ref).split(":", 1)[1]))
            if contract is not None:
                location_id = contract.start_location_id
                unit_price = float(layer.unit_components.materials_bought)
                for item in contract.items:
                    if int(item.type_id) == int(layer.type_id) and item.freight_rate_per_m3 is not None:
                        rate = float(item.freight_rate_per_m3)
                        break
        elif layer is not None:
            # Opening / unknown / produced: the unit cost that was actually used.
            unit_price = float(layer.unit_cost)
        return (
            kind, ref, seq, int(draw.layer_id), int(draw.type_id), str(draw.unit),
            qty, str(draw.origin), None if layer is None else layer.source_ref,
            *_components(draw.components),
            (draw.components.total / qty) if qty else 0.0,
            bool(draw.estimated_origin), bool(draw.price_missing),
            bool(layer.assumed_me) if layer is not None else False,
            bool(layer.assumed_decryptor) if layer is not None else False,
            None if layer is None else layer.layer_date,
            None if layer is None else layer.job_id,
            location_id, unit_price, rate,
            None if layer is None or layer.source is None else str(layer.source),
            channel,
        )

    rows = []
    for sale in result.realized_results:
        for seq, draw in enumerate(sale.draws):
            rows.append(one("sale", sale.sale_ref, seq, draw, sale.channel))
    for job in result.jobs:
        for seq, draw in enumerate(job.draws):
            rows.append(one("job", job.job_ref, seq, draw, None))
    return rows


def _job_rows(result, bonus_by_job: dict) -> list[tuple]:
    layers = {int(layer.layer_id): layer for layer in result.all_layers}
    rows = []
    for job in result.jobs:
        left = None
        if job.output_layer_id is not None:
            layer = layers.get(int(job.output_layer_id))
            if layer is not None:
                left = float(layer.quantity_left)
        applied = bonus_by_job.get((job.owner_type, int(job.owner_id), int(job.job_id)))
        no_structure_bonus = None if applied is None else (not applied)
        rows.append((
            job.job_ref, int(job.job_id), job.owner_type, int(job.owner_id),
            int(job.activity_id), int(job.blueprint_type_id),
            None if job.product_type_id is None else int(job.product_type_id),
            int(job.runs), job.successful_runs, job.start_date, job.end_date,
            job.status, job.state, float(job.job_cost),
            *_components(job.components),
            bool(job.assumed_me), bool(job.assumed_decryptor), bool(job.price_missing),
            no_structure_bonus, job.output_layer_id, job.output_type_id,
            float(job.output_quantity), left, job.output_unit, job.facility_id,
        ))
    return rows


def _fee_rows(result) -> list[tuple]:
    rows = [
        ("without_sale", p.owner_type, int(p.owner_id), p.day, float(p.amount), None)
        for p in result.fees_without_sale
    ]
    rows.extend(
        ("other_trading", f.owner_type, int(f.owner_id), f.day, float(f.amount), f.sale_ref)
        for f in result.other_trading_fees
    )
    return rows


def _loss_rows(result) -> list[tuple]:
    rows = []
    for kind, items in (
        ("cancelled", result.cancelled_job_losses),
        ("failed_invention", result.failed_invention_losses),
    ):
        for loss in items:
            rows.append((
                kind, loss.job_ref, int(loss.job_id), loss.date, *_components(loss.components),
            ))
    return rows


def _drift_rows(result, esi_stock: dict) -> list[tuple]:
    ledger: dict[int, float] = defaultdict(float)
    for layer in result.layers:
        if layer.unit == UNIT_ITEM:
            ledger[int(layer.type_id)] += float(layer.quantity_left)
    esi = {int(k): float(v) for k, v in (esi_stock or {}).items()}
    rows = []
    for type_id in sorted(set(ledger) | set(esi)):
        ledger_qty = ledger.get(type_id, 0.0)
        esi_qty = esi.get(type_id, 0.0)
        if abs(ledger_qty) <= 1e-9 and abs(esi_qty) <= 1e-9:
            continue
        rows.append((type_id, ledger_qty, esi_qty, esi_qty - ledger_qty))
    return rows


def _opening_rows(result) -> list[tuple]:
    return [
        (int(line.type_id), float(line.raw_quantity), float(line.quantity),
         float(line.unit_cost), str(line.price_source),
         None if line.reference_price is None else float(line.reference_price))
        for line in result.opening_lines
    ]


def _frozen_rows(snapshot, result, scope_hash: str) -> list[tuple]:
    """Types whose opening quantity was computed in this run (not frozen
    yet). Zero quantities are frozen too, so a type that had no opening
    stock never gets one later."""
    frozen = snapshot.frozen_opening or {}
    return [
        (int(tid), float(raw), snapshot.t0, scope_hash)
        for tid, raw in sorted(result.opening_raw.items())
        if int(tid) not in frozen
    ]


def _persist(snapshot, result, scope_hash: str) -> tuple[int, list]:
    """Write derived tables. Returns (run_id, warnings). The run row is
    written only inside replace_derived_costing, after the rows succeed."""
    bonus = db.structure_bonus_applied_by_job(access.shared_owners("industry_jobs"))
    extra = list(result.warnings)
    for ref in result.jobs_before_t0:
        extra.append(f"job_before_t0: {ref} started before cutover and consumes nothing")
    for ref in result.skipped_corp_jobs:
        extra.append(
            f"skipped_corp_job: {ref} installer is not one of this tenant's characters"
        )
    run_id = db.replace_derived_costing(
        layers=[_layer_row(layer) for layer in result.layers],
        results=[_result_row(row) for row in result.realized_results],
        draws=_draw_rows(result, snapshot),
        jobs=_job_rows(result, bonus),
        fee_lines=_fee_rows(result),
        losses=_loss_rows(result),
        capital=[(int(tid), float(amt)) for tid, amt in result.capital_in_bpos.items()],
        drift=_drift_rows(result, snapshot.esi_stock_by_type),
        cutover_t0=snapshot.t0,
        warnings=extra,
        opening_lines=_opening_rows(result),
        frozen_opening=_frozen_rows(snapshot, result, scope_hash),
        reset_frozen_opening=snapshot.frozen_opening is None,
        owner_scope_hash=scope_hash,
        costing_version=COSTING_VERSION,
    )
    return run_id, extra


def rebuild_realized(reset_opening: bool = False) -> dict:
    """Load ledger → pure costing → replace derived tables.

    Returns a summary dict (counts, t0, run_id, warnings). Does not raise
    ActionError - callers in Phase 3 wrap that. An empty archive (no T0)
    clears derived tables and records a run with a warning.
    """
    scope_hash = access.owner_scope_hash()
    reset_opening = _opening_reset_for_version(reset_opening)
    snapshot = load_ledger_snapshot(scope_hash=scope_hash, reset_opening=reset_opening)
    if snapshot is None:
        warnings = ["no wallet archive yet; cutover T0 unknown"]
        run_id = db.replace_derived_costing(
            layers=[], results=[], cutover_t0=None, warnings=warnings,
            reset_frozen_opening=True, owner_scope_hash=scope_hash,
            costing_version=COSTING_VERSION,
        )
        return {
            "run_id": run_id,
            "cutover_t0": None,
            "layers": 0,
            "realized_results": 0,
            "warnings": warnings,
        }

    result = _run_costing(snapshot)
    run_id, extra_warnings = _persist(snapshot, result, scope_hash)
    return {
        "run_id": run_id,
        "cutover_t0": snapshot.t0.isoformat(),
        "layers": len(result.layers),
        "realized_results": len(result.realized_results),
        "jobs": len(result.jobs),
        "cancelled_losses": len(result.cancelled_job_losses),
        "coverage": result.coverage(),
        "estimated_results": sum(1 for r in result.realized_results if r.estimated),
        "warnings": extra_warnings,
        "opening_frozen": snapshot.frozen_opening is not None,
        "wallet_transactions": len(snapshot.wallet_transactions),
        "industry_jobs": len(snapshot.industry_jobs),
        "journal_entries": len(snapshot.journal_entries),
    }

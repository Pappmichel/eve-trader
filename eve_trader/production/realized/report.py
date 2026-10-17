"""Aggregation for the realized-profit API.

Reads the derived tables a rebuild already wrote. It does not run costing
again. Flags stored on those rows (`estimated`, `price_missing`,
`assumed_me`, `assumed_decryptor`, coverage) are copied through; a missing
value stays missing.

Fee pools are whole UTC days (D12). A day is included when it overlaps the
period. Capital in BPOs and inventory at cost are balances as of the latest
rebuild, not flows inside the period. Sales tax is listed in the cost
structure and is already removed from revenue, so it is not added to COGS.

D11: a run stores the fingerprint of the owners shared with Production.
When the current sharing no longer matches, every read returns nothing
from that run (`sharing_changed`) until the next rebuild.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ... import storage
from . import access, db, names as display_names
from .costing import ACTIVITY_INVENTION, ESTIMATED_COVERAGE_THRESHOLD, CostComponents

_EPS = 1e-9
_CHAIN_DEPTH = 24
FREIGHT_LABEL = "configured rate"

# Section 9a warning groups. Always present, even when empty, so the page
# shape does not depend on which warnings happened to fire.
SECTION_9A_KINDS = (
    "inventory_drift",
    "trading_overlap",
    "archive_gap",
    "outside_home_structure",
    "unresolved_region",
    "unmatched_broker_fee",
    "assumed_me",
    "assumed_decryptor",
    "negative_opening",
)

_GAP_SHARE_KIND = {
    "wallet_journal": "wallet",
    "wallet_transactions": "wallet",
    "industry_jobs": "industry_jobs",
    "market_orders": "market_orders",
    "contracts": "contracts",
}

COST_STRUCTURE_LABELS = (
    ("materials_bought", "Materials bought"),
    ("materials_estimated", "Materials estimated"),
    ("freight", f"Freight ({FREIGHT_LABEL})"),
    ("job", "Job costs"),
    ("invention", "Invention"),
    ("broker_fees", "Broker fees"),
    ("sales_tax", "Sales tax"),
)


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _num(value) -> float:
    return 0.0 if value is None else float(value)


def _warnings(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _components_from(row, offset: int) -> CostComponents:
    return CostComponents(
        materials_bought=_num(row[offset]),
        materials_estimated=_num(row[offset + 1]),
        freight=_num(row[offset + 2]),
        job=_num(row[offset + 3]),
        invention=_num(row[offset + 4]),
        fees=_num(row[offset + 5]),
    )


def _coverage(components: CostComponents, price_missing: bool) -> tuple[float, bool]:
    """Same rule as RealizedResult: freight is outside coverage; a missing
    price forces estimated even when the ratio would look complete."""
    cov = components.coverage()
    if cov is None:
        cov = 0.0 if price_missing else 1.0
    estimated = bool(price_missing) or cov < ESTIMATED_COVERAGE_THRESHOLD
    return cov, estimated


def _cost_structure(components: CostComponents, sales_tax: float) -> list[dict]:
    amounts = {
        "materials_bought": components.materials_bought,
        "materials_estimated": components.materials_estimated,
        "freight": components.freight,
        "job": components.job,
        "invention": components.invention,
        "broker_fees": components.fees,
        "sales_tax": sales_tax,
    }
    return [
        {"key": key, "label": label, "amount": amounts[key]}
        for key, label in COST_STRUCTURE_LABELS
    ]


def _flags(*, estimated: bool, price_missing: bool, assumed_me: bool = False,
           assumed_decryptor: bool = False, coverage: Optional[float] = None,
           tax_missing: bool = False, detail_missing: bool = False,
           no_structure_bonus: Optional[bool] = None) -> dict:
    return {
        "estimated": bool(estimated),
        "price_missing": bool(price_missing),
        "assumed_me": bool(assumed_me),
        "assumed_decryptor": bool(assumed_decryptor),
        "coverage": coverage,
        "tax_missing": bool(tax_missing),
        "detail_missing": bool(detail_missing),
        "no_structure_bonus": no_structure_bonus,
    }


def _names(type_ids: set[int]) -> dict[int, str]:
    if not type_ids:
        return {}
    return storage.sde_type_names(type_ids)


def _window(period_start: Optional[datetime], period_end: datetime, since_t0: bool,
            t0: Optional[datetime]) -> Optional[tuple[datetime, datetime]]:
    if since_t0:
        if t0 is None:
            return None
        return _as_utc(t0), period_end
    assert period_start is not None
    return period_start, period_end


def _empty_summary(period: dict, status: dict) -> dict:
    zero = CostComponents()
    return {
        "period": period,
        "status": status,
        "revenue": 0.0,
        "cost_of_goods_sold": 0.0,
        "realized_profit": 0.0,
        "margin": None,
        "coverage": None,
        "estimated": False,
        "units_sold": 0.0,
        "price_missing": False,
        "tax_missing_sales": 0,
        "fees_without_sale": 0.0,
        "fees_on_other_trading": 0.0,
        "capital_in_bpos": 0.0,
        "inventory_at_cost": 0.0,
        "cancelled_job_losses": 0.0,
        "failed_invention_losses": 0.0,
        "manual_costs": 0.0,
        "manual_revenues": 0.0,
        "profit_including_manual": 0.0,
        "cost_structure": _cost_structure(zero, 0.0),
        "by_day": [],
        "balances_as_of_rebuild": ["capital_in_bpos", "inventory_at_cost"],
        "flags": _flags(estimated=False, price_missing=False, coverage=None),
    }


def _run_state() -> tuple[Optional[tuple], Optional[datetime], bool]:
    """(latest run, its T0, sharing_changed). `sharing_changed` is True when
    the owners shared with Production differ from the ones the run was built
    from (or the run predates the fingerprint); its rows must not be shown."""
    run = db.latest_ledger_run()
    if run is None:
        return None, None, False
    t0 = None if run[2] is None else _as_utc(run[2])
    changed = run[4] is None or run[4] != access.owner_scope_hash()
    return run, t0, changed


def _status(run, t0, archive_since, wallet_sync, jobs_sync, results_ever: int,
            *, sharing_changed: bool = False, last_sync: Optional[datetime] = None) -> dict:
    stale = False
    if run is not None and last_sync is not None and run[1] is not None:
        stale = _as_utc(last_sync) > _as_utc(run[1])
    if run is None:
        hint = "Recompute to build realized profit from the archive."
        empty = True
    elif sharing_changed:
        hint = "Sharing with Production changed since the last recompute. Recompute to update."
        empty = True
    elif t0 is None:
        hint = "no wallet archive yet; cutover T0 unknown"
        empty = True
    elif results_ever == 0:
        since = _iso(archive_since) or "the first sync"
        hint = f"archive running since {since}; results start at {_iso(t0)}"
        empty = True
    else:
        hint = None
        empty = False
    return {
        "empty": empty,
        "needs_rebuild": run is None or sharing_changed,
        "sharing_changed": bool(sharing_changed),
        "stale": stale,
        "hint": hint,
        "cutover_t0": _iso(t0),
        "archive_since": _iso(archive_since),
        "last_recompute": None if run is None else _iso(run[1]),
        "last_wallet_sync": _iso(wallet_sync),
        "last_jobs_sync": _iso(jobs_sync),
        "run_id": None if run is None else int(run[0]),
    }


def _sync_times() -> tuple[Optional[datetime], Optional[datetime], Optional[datetime], Optional[datetime]]:
    """(archive_since, last wallet sync, last jobs sync, last sync of any
    archive kind) over owners currently shared with Production."""
    shared = {kind: set(access.shared_owners(kind)) for kind in set(_GAP_SHARE_KIND.values())}
    wallet_sync = None
    jobs_sync = None
    any_sync = None
    for owner_type, owner_id, kind, synced in db.list_ledger_sync_state():
        key = (owner_type, int(owner_id))
        share_kind = _GAP_SHARE_KIND.get(str(kind))
        if share_kind is None or key not in shared[share_kind] or synced is None:
            continue
        if any_sync is None or synced > any_sync:
            any_sync = synced
        if share_kind == "wallet" and (wallet_sync is None or synced > wallet_sync):
            wallet_sync = synced
        elif share_kind == "industry_jobs" and (jobs_sync is None or synced > jobs_sync):
            jobs_sync = synced
    archive_since = db.earliest_archived_transaction(list(shared["wallet"]))
    return archive_since, wallet_sync, jobs_sync, any_sync


def _period_dict(start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    return {
        "start": _iso(start),
        "end": _iso(end),
        "since_t0": since_t0,
        "days": days,
    }


def summary(*, start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    with storage.batch_session():
        run, t0, changed = _run_state()
        archive_since, wallet_sync, jobs_sync, any_sync = _sync_times()
        results_ever = db.count_realized_results() if run is not None else 0
        status = _status(run, t0, archive_since, wallet_sync, jobs_sync, results_ever,
                         sharing_changed=changed, last_sync=any_sync)
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        period = _period_dict(None if window is None else window[0], end, since_t0, days)
        if window is None:
            return _empty_summary(period, status)
        start_at, end_at = window
        totals = db.realized_totals(start_at, end_at)
        components = CostComponents(
            materials_bought=_num(totals[4]), materials_estimated=_num(totals[5]),
            freight=_num(totals[6]), job=_num(totals[7]), invention=_num(totals[8]),
            fees=_num(totals[9]),
        )
        price_missing = bool(totals[12])
        sales_count = int(totals[13])
        cov, estimated = _coverage(components, price_missing) if sales_count else (None, False)
        revenue = _num(totals[0])
        cost = _num(totals[1])
        profit = _num(totals[2])
        tax = _num(totals[10])
        tax_missing_sales = int(totals[11]) if sales_count else 0
        fee_start = start_at.date()
        # Exclusive end: a fee day is in the period when it is >= start's UTC
        # day and < the UTC day after the last instant included.
        fee_end = (end_at - timedelta(microseconds=1)).date() + timedelta(days=1)
        fees_without, fees_other = db.fee_totals_between(fee_start, fee_end)
        cancelled, failed = db.loss_totals_between(start_at, end_at)
        manual_costs, manual_revenues = db.manual_entry_totals(start_at, end_at)
        capital = db.capital_in_bpos_total()
        inventory = db.inventory_at_cost_total()
        by_day = [
            {"date": _iso(row[0]), "revenue": _num(row[1]), "cost": _num(row[2]), "profit": _num(row[3])}
            for row in db.realized_by_day(start_at, end_at)
        ]
    flags = _flags(
        estimated=estimated, price_missing=price_missing, coverage=cov,
        tax_missing=tax_missing_sales > 0,
    )
    return {
        "period": period,
        "status": status,
        "revenue": revenue,
        "cost_of_goods_sold": cost,
        "realized_profit": profit,
        "margin": (profit / revenue) if revenue else None,
        "coverage": cov,
        "estimated": estimated,
        "units_sold": _num(totals[3]),
        "price_missing": price_missing,
        "tax_missing_sales": tax_missing_sales,
        "fees_without_sale": fees_without,
        "fees_on_other_trading": fees_other,
        "capital_in_bpos": capital,
        "inventory_at_cost": inventory,
        "cancelled_job_losses": cancelled,
        "failed_invention_losses": failed,
        "manual_costs": manual_costs,
        "manual_revenues": manual_revenues,
        "profit_including_manual": profit + manual_revenues - manual_costs,
        "cost_structure": _cost_structure(components, tax),
        "by_day": by_day,
        "balances_as_of_rebuild": ["capital_in_bpos", "inventory_at_cost"],
        "flags": flags,
    }


def _product_row(type_id: int, totals: tuple, assumed: tuple, warnings: list[str], names: dict,
                 planned: Optional[tuple] = None) -> dict:
    components = CostComponents(
        materials_bought=_num(totals[5]), materials_estimated=_num(totals[6]),
        freight=_num(totals[7]), job=_num(totals[8]), invention=_num(totals[9]),
        fees=_num(totals[10]),
    )
    price_missing = bool(totals[11])
    qty = _num(totals[1])
    revenue = _num(totals[2])
    cost = _num(totals[3])
    profit = _num(totals[4])
    cov, estimated = _coverage(components, price_missing)
    covered_qty, covered_cost, covered_realized = planned or (0.0, 0.0, 0.0)
    planned_cost = (covered_cost / covered_qty) if covered_qty > _EPS else None
    # Realized minus planned over the same units (those with a planned value).
    planned_difference = (
        (covered_realized - covered_cost) / covered_qty if covered_qty > _EPS else None
    )
    planned_coverage = (covered_qty / qty) if qty > _EPS else None
    return {
        "type_id": int(type_id),
        "type_name": names.get(int(type_id)),
        "units_sold": qty,
        "revenue": revenue,
        "cost": cost,
        "cost_per_unit": (cost / qty) if qty else None,
        "net_sell_per_unit": (revenue / qty) if qty else None,
        "profit": profit,
        "margin": (profit / revenue) if revenue else None,
        "coverage": cov,
        "planned_cost_per_unit": planned_cost,
        "planned_coverage": planned_coverage,
        "planned_difference_per_unit": planned_difference,
        "warnings": sorted(set(warnings)),
        "flags": _flags(
            estimated=estimated, price_missing=price_missing,
            assumed_me=bool(assumed[0]), assumed_decryptor=bool(assumed[1]),
            coverage=cov, tax_missing="tax_missing" in warnings,
        ),
    }


def products(*, start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    with storage.batch_session():
        run, t0, changed = _run_state()
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        period = _period_dict(None if window is None else window[0], end, since_t0, days)
        if window is None:
            return {"period": period, "rows": [], "needs_rebuild": run is None or changed}
        start_at, end_at = window
        rows = db.realized_by_type(start_at, end_at)
        flags = {int(r[0]): (bool(r[1]), bool(r[2])) for r in db.realized_draw_flags_by_type(start_at, end_at)}
        warn: dict[int, list[str]] = defaultdict(list)
        for type_id, text in db.realized_warnings_by_type(start_at, end_at):
            warn[int(type_id)].append(str(text))
        names = _names({int(r[0]) for r in rows})
        planned = db.planned_unit_cost_by_type(start_at, end_at)
    return {
        "period": period,
        "needs_rebuild": False,
        "rows": [
            _product_row(
                int(row[0]), row, flags.get(int(row[0]), (False, False)),
                warn.get(int(row[0]), []), names, planned.get(int(row[0])),
            )
            for row in rows
        ],
    }


def _sale_dict(row, names: dict) -> dict:
    components = _components_from(row, 15)
    price_missing = bool(row[12])
    estimated = bool(row[13])
    qty = _num(row[2])
    revenue = _num(row[3])
    warnings = _warnings(row[14])
    cov = _num(row[6])
    return {
        "sale_ref": str(row[0]),
        "type_id": int(row[1]),
        "type_name": names.get(int(row[1])),
        "quantity": qty,
        "revenue": revenue,
        "cost": _num(row[4]),
        "profit": _num(row[5]),
        "coverage": cov,
        "sale_date": _iso(row[7]),
        "gross": _num(row[8]),
        "sales_tax": None if row[9] is None else float(row[9]),
        "broker_fee": _num(row[10]),
        "location_id": None if row[11] is None else int(row[11]),
        "net_price": (revenue / qty) if qty else None,
        "warnings": warnings,
        "cost_structure": _cost_structure(components, 0.0 if row[9] is None else float(row[9])),
        "flags": _flags(
            estimated=estimated, price_missing=price_missing,
            coverage=cov, tax_missing="tax_missing" in warnings,
        ),
        "source": None if len(row) <= 21 or row[21] is None else str(row[21]),
    }


def product(*, type_id: int, start: Optional[datetime], end: datetime,
            since_t0: bool, days: Optional[int]) -> dict:
    listing = products(start=start, end=end, since_t0=since_t0, days=days)
    match = next((row for row in listing["rows"] if row["type_id"] == int(type_id)), None)
    with storage.batch_session():
        run, t0, changed = _run_state()
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        sales = [] if window is None else db.list_sales_for_type(int(type_id), window[0], window[1])
        names = _names({int(type_id)} | {int(row[1]) for row in sales})
    if match is None:
        match = {
            "type_id": int(type_id),
            "type_name": names.get(int(type_id)),
            "units_sold": 0.0,
            "revenue": 0.0,
            "cost": 0.0,
            "cost_per_unit": None,
            "net_sell_per_unit": None,
            "profit": 0.0,
            "margin": None,
            "coverage": None,
            "warnings": [],
            "flags": _flags(estimated=False, price_missing=False, coverage=None),
        }
    return {
        "period": listing["period"],
        "needs_rebuild": listing["needs_rebuild"],
        "product": match,
        "sales": [_sale_dict(row, names) for row in sales],
    }


def _draw_flags(row, *, detail_missing: bool = False) -> dict:
    price_missing = bool(row[16])
    estimated_origin = bool(row[15])
    estimated = price_missing or estimated_origin
    return _flags(
        estimated=estimated, price_missing=price_missing,
        assumed_me=bool(row[17]), assumed_decryptor=bool(row[18]),
        detail_missing=detail_missing,
    )


def _leaf(row, scale: float = 1.0) -> dict:
    """One input draw. `scale` is the share of the consuming job that this
    sale accounts for, so quantities and freight add up to the sale."""
    origin = str(row[6])
    qty = _num(row[5]) * scale
    estimated_origin = bool(row[15])
    node = {
        "kind": "estimate" if origin in ("opening", "unknown") else "purchase" if origin == "bought" else origin,
        "origin": origin,
        "type_id": int(row[3]),
        "quantity": qty,
        "unit": str(row[4]),
        "unit_cost": _num(row[14]),
        "unit_price": None if row[22] is None else float(row[22]),
        # Only an estimate is valued at a reference price; an opening
        # override is the user's own purchase price.
        "reference_price": None if not estimated_origin or row[22] is None else float(row[22]),
        "date": _iso(row[19]),
        "location_id": None if row[21] is None else int(row[21]),
        "freight": _num(row[10]) * scale,
        "freight_label": FREIGHT_LABEL if origin == "bought" else None,
        "freight_rate_per_m3": None if row[23] is None else float(row[23]),
        "source_ref": row[7],
        "job_id": None if row[20] is None else int(row[20]),
        "flags": _draw_flags(row, detail_missing=origin == "produced"),
        "children": [],
        "source": None if len(row) <= 24 or row[24] is None else str(row[24]),
    }
    return node


def _job_node(job_row, draw_row, children: list, scale: float = 1.0,
              share: Optional[float] = None) -> dict:
    """`scale`: share of the parent's draw this sale accounts for. `share`:
    the fraction of this job's output (and so of its inputs and costs)
    that ends up in the sale."""
    components = CostComponents(
        materials_bought=_num(job_row[12]), materials_estimated=_num(job_row[13]),
        freight=_num(job_row[14]), job=_num(job_row[15]), invention=_num(job_row[16]),
        fees=_num(job_row[17]),
    )
    return {
        "kind": "job",
        "origin": "produced",
        "type_id": int(draw_row[3]),
        "quantity": _num(draw_row[5]) * scale,
        "unit": str(draw_row[4]),
        "unit_cost": _num(draw_row[14]),
        "job_ref": str(job_row[0]),
        "job_id": int(job_row[1]),
        "activity_id": int(job_row[2]),
        "runs": int(job_row[5]),
        "successful_runs": None if job_row[6] is None else int(job_row[6]),
        "date": _iso(job_row[7]),
        "end_date": _iso(job_row[8]),
        "state": str(job_row[10]),
        "job_cost": _num(job_row[11]),
        "facility_id": None if job_row[27] is None else int(job_row[27]),
        "output_quantity": _num(job_row[24]),
        "flags": _flags(
            estimated=bool(draw_row[15]) or bool(draw_row[16]) or bool(job_row[20]),
            price_missing=bool(draw_row[16]) or bool(job_row[20]),
            assumed_me=bool(job_row[18]) or bool(draw_row[17]),
            assumed_decryptor=bool(job_row[19]) or bool(draw_row[18]),
            no_structure_bonus=None if job_row[21] is None else bool(job_row[21]),
        ),
        "cost_total": components.total,
        "share": share,
        "cost_share": None if share is None else components.total * share,
        "children": children,
    }


def _children(draws: list[tuple], depth: int, stack: set[str], scale: float = 1.0) -> list[dict]:
    """Expand draws into nodes. A job's draws are the whole job's inputs;
    `scale` (the share of that job this sale accounts for) brings them down
    to what the sale actually consumed."""
    if depth >= _CHAIN_DEPTH:
        return [
            {**_leaf(row, scale), "flags": _draw_flags(row, detail_missing=True)}
            for row in draws
        ]
    produced_ids = [int(row[2]) for row in draws if str(row[6]) == "produced"]
    jobs = {int(job[22]): job for job in db.list_jobs_by_output_layers(produced_ids)}
    next_refs = []
    for row in draws:
        if str(row[6]) != "produced":
            continue
        job = jobs.get(int(row[2]))
        if job is not None and str(job[0]) not in stack:
            next_refs.append(str(job[0]))
    grouped: dict[str, list] = defaultdict(list)
    for draw in db.list_draws("job", next_refs):
        grouped[str(draw[0])].append(draw)
    nodes = []
    for row in draws:
        if str(row[6]) != "produced":
            nodes.append(_leaf(row, scale))
            continue
        job = jobs.get(int(row[2]))
        if job is None:
            nodes.append(_leaf(row, scale))
            continue
        ref = str(job[0])
        output_qty = _num(job[24])
        share = (_num(row[5]) * scale / output_qty) if output_qty > _EPS else None
        if ref in stack:
            nodes.append(_job_node(job, row, [], scale, share))
            node_flags = nodes[-1]["flags"]
            node_flags["detail_missing"] = True
            continue
        inputs = _children(grouped.get(ref, []), depth + 1, stack | {ref},
                           share if share is not None else scale)
        if share is None:
            for child in inputs:
                child["flags"]["detail_missing"] = True
        nodes.append(_job_node(job, row, inputs, scale, share))
    return nodes


def sale_chain(sale_ref: str) -> Optional[dict]:
    """None when the sale does not exist or the run is hidden (D11)."""
    with storage.batch_session():
        run, _t0, changed = _run_state()
        if run is None or changed:
            return None
        row = db.get_realized_sale(sale_ref)
        if row is None:
            return None
        draws = db.list_draws("sale", [sale_ref])
        names = _names({int(row[1])} | {int(d[3]) for d in draws})
        children = _children(draws, 0, set())
        sale = _sale_dict(row, names)
    for node in _walk(children):
        tid = node.get("type_id")
        if tid is not None:
            node["type_name"] = names.get(int(tid)) if int(tid) in names else node.get("type_name")
    # Names for inputs discovered deeper than the first sale draws.
    missing = {int(node["type_id"]) for node in _walk(children) if node.get("type_id") is not None} - set(names)
    if missing:
        extra = _names(missing)
        for node in _walk(children):
            tid = node.get("type_id")
            if tid is not None and node.get("type_name") is None:
                node["type_name"] = extra.get(int(tid))
    tree = {
        "kind": "product",
        "origin": None,
        "type_id": int(row[1]),
        "type_name": names.get(int(row[1])),
        "quantity": _num(row[2]),
        "sale_ref": sale_ref,
        "flags": sale["flags"],
        "children": children,
    }
    return {"sale": sale, "tree": tree}


def _walk(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


JOB_STATES = ("all", "finished", "running")


def jobs(*, start: Optional[datetime] = None, end: Optional[datetime] = None,
         state: str = "all") -> dict:
    """Costed jobs since T0 (jobs before T0 are a warning, not a row).

    `state`: "finished" (everything but wip), "running" (wip) or "all".
    `start`/`end` limit finished jobs to those that ended in [start, end);
    running jobs are never cut by the period. `groups` sums the listed jobs
    per (activity, output type); `totals` sums them all.
    """
    empty = {"cutover_t0": None, "needs_rebuild": True, "rows": [], "groups": [], "totals": _job_totals([])}
    with storage.batch_session():
        run, t0, changed = _run_state()
        if run is None or changed:
            return {**empty, "cutover_t0": _iso(t0)}
        rows = [r for r in db.list_job_records() if _job_in_view(r, start, end, state)]
        planned = db.planned_unit_costs_by_job()
        contract_jobs = db.job_refs_with_contract_draws()
        names = _names(
            {int(r[6]) for r in rows if r[6] is not None}
            | {int(r[5]) for r in rows}
            | {int(r[24]) for r in rows if r[24] is not None}
        )
        owners = display_names.owner_names({int(r[3]) for r in rows})
        facilities = display_names.location_names({int(r[29]) for r in rows if r[29] is not None})
    out = []
    for row in rows:
        components = CostComponents(
            materials_bought=_num(row[14]), materials_estimated=_num(row[15]),
            freight=_num(row[16]), job=_num(row[17]), invention=_num(row[18]),
            fees=_num(row[19]),
        )
        output_qty = _num(row[26])
        left = None if row[27] is None else float(row[27])
        sold_share = None
        if row[12] == "produced" and output_qty > _EPS and left is not None:
            sold_share = (output_qty - left) / output_qty
        unit_cost = (components.total / output_qty) if output_qty > _EPS else None
        activity = int(row[4])
        out.append({
            "job_ref": str(row[0]),
            "job_id": int(row[1]),
            "owner_type": str(row[2]),
            "owner_id": int(row[3]),
            "owner_name": owners.get(int(row[3])),
            "activity_id": activity,
            "blueprint_type_id": int(row[5]),
            "blueprint_name": names.get(int(row[5])),
            "product_type_id": None if row[6] is None else int(row[6]),
            "product_name": None if row[6] is None else names.get(int(row[6])),
            "runs": int(row[7]),
            "successful_runs": None if row[8] is None else int(row[8]),
            "start_date": _iso(row[9]),
            "end_date": _iso(row[10]),
            "status": row[11],
            "state": str(row[12]),
            "job_cost": _num(row[13]),
            "input_cost": components.total - _num(row[13]),
            "unit_cost": unit_cost,
            "output_quantity": output_qty,
            "output_quantity_left": left,
            "output_unit": row[28],
            "sold_share": sold_share,
            "cost_per_bpc_run": unit_cost if activity == ACTIVITY_INVENTION and unit_cost is not None else None,
            "facility_id": None if row[29] is None else int(row[29]),
            "facility_name": None if row[29] is None else facilities.get(int(row[29])),
            "planned_unit_cost": planned.get((str(row[2]), int(row[3]), int(row[1]))),
            "contract_input": str(row[0]) in contract_jobs,
            "cost_structure": _cost_structure(components, 0.0),
            "flags": _flags(
                estimated=bool(row[22]) or bool(row[20]) or bool(row[21]),
                price_missing=bool(row[22]),
                assumed_me=bool(row[20]),
                assumed_decryptor=bool(row[21]),
                no_structure_bonus=None if row[23] is None else bool(row[23]),
            ),
        })
    return {
        "cutover_t0": _iso(t0), "needs_rebuild": False, "rows": out,
        "groups": _job_groups(out), "totals": _job_totals(out),
    }


def _job_in_view(row, start: Optional[datetime], end: Optional[datetime], state: str) -> bool:
    running = str(row[12]) == "wip"
    if state == "running":
        return running
    if state == "finished" and running:
        return False
    if running or (start is None and end is None):
        return True
    ended = row[10]
    if ended is None:
        return False
    ended = _as_utc(ended)
    return (start is None or ended >= start) and (end is None or ended < end)


def _job_totals(rows: list[dict]) -> dict:
    return {
        "jobs": len(rows),
        "input_cost": sum(r["input_cost"] for r in rows),
        "job_cost": sum(r["job_cost"] for r in rows),
        "total_cost": sum(r["input_cost"] + r["job_cost"] for r in rows),
        "failed": sum(1 for r in rows if r["state"] == "failed"),
        "cancelled": sum(1 for r in rows if r["state"] == "cancelled"),
        "running": sum(1 for r in rows if r["state"] == "wip"),
    }


def _job_groups(rows: list[dict]) -> list[dict]:
    """One row per (activity, output type). Unit cost is the produced jobs'
    cost over their output; planned unit cost is weighted by the output of
    the jobs that have one, and the difference compares those same jobs."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["activity_id"], r["product_type_id"] if r["product_type_id"] is not None else r["blueprint_type_id"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "activity_id": r["activity_id"],
                "type_id": key[1],
                "type_name": r["product_name"] or r["blueprint_name"],
                "output_unit": r["output_unit"],
                "jobs": 0, "runs": 0, "successful_runs": 0, "running": 0, "failed": 0, "cancelled": 0,
                "input_cost": 0.0, "job_cost": 0.0, "output_quantity": 0.0, "output_left": 0.0,
                "_produced_cost": 0.0, "_planned_qty": 0.0, "_planned_cost": 0.0, "_planned_actual": 0.0,
                "estimated": False, "assumed": False,
            }
        cost = r["input_cost"] + r["job_cost"]
        g["jobs"] += 1
        g["runs"] += r["runs"]
        g["successful_runs"] += r["successful_runs"] or 0
        g["running"] += r["state"] == "wip"
        g["failed"] += r["state"] == "failed"
        g["cancelled"] += r["state"] == "cancelled"
        g["input_cost"] += r["input_cost"]
        g["job_cost"] += r["job_cost"]
        g["estimated"] = g["estimated"] or bool(r["flags"]["estimated"])
        g["assumed"] = g["assumed"] or bool(r["flags"]["assumed_me"] or r["flags"]["assumed_decryptor"])
        if r["state"] == "produced" and r["output_quantity"] > _EPS:
            qty = r["output_quantity"]
            g["output_quantity"] += qty
            g["output_left"] += r["output_quantity_left"] if r["output_quantity_left"] is not None else qty
            g["_produced_cost"] += cost
            if r.get("planned_unit_cost") is not None:
                g["_planned_qty"] += qty
                g["_planned_cost"] += qty * r["planned_unit_cost"]
                g["_planned_actual"] += cost
    out = []
    for g in groups.values():
        qty = g["output_quantity"]
        pq = g.pop("_planned_qty")
        pc = g.pop("_planned_cost")
        pa = g.pop("_planned_actual")
        produced_cost = g.pop("_produced_cost")
        left = g.pop("output_left")
        g["total_cost"] = g["input_cost"] + g["job_cost"]
        g["unit_cost"] = produced_cost / qty if qty > _EPS else None
        g["sold_share"] = (qty - left) / qty if qty > _EPS else None
        g["planned_unit_cost"] = pc / pq if pq > _EPS else None
        g["planned_difference_per_unit"] = (pa - pc) / pq if pq > _EPS else None
        # Every attempt's cost (failed ones included) over the BPC runs the
        # successful ones produced.
        g["cost_per_bpc_run"] = (
            g["total_cost"] / qty if g["activity_id"] == ACTIVITY_INVENTION and qty > _EPS else None
        )
        out.append(g)
    out.sort(key=lambda g: -g["total_cost"])
    return out


def inventory() -> dict:
    with storage.batch_session():
        run, _t0, changed = _run_state()
        if run is None or changed:
            return {"needs_rebuild": True, "rows": []}
        layers = db.list_remaining_layers()
        drift_rows = {int(r[0]): r for r in db.list_inventory_drift()}
        grouped: dict[tuple, dict] = {}
        for row in layers:
            key = (int(row[0]), str(row[1]))
            slot = grouped.get(key)
            if slot is None:
                slot = {
                    "type_id": int(row[0]),
                    "unit": str(row[1]),
                    "quantity": 0.0,
                    "total": 0.0,
                    "origin_mix": {"bought": 0.0, "produced": 0.0, "opening": 0.0, "unknown": 0.0},
                    "assumed_me": False,
                    "assumed_decryptor": False,
                    "estimated_origin": False,
                    "price_missing": False,
                }
                grouped[key] = slot
            qty = _num(row[2])
            slot["quantity"] += qty
            slot["total"] += qty * _num(row[3])
            origin = str(row[4])
            if origin in slot["origin_mix"]:
                slot["origin_mix"][origin] += qty
            slot["assumed_me"] = slot["assumed_me"] or bool(row[5])
            slot["assumed_decryptor"] = slot["assumed_decryptor"] or bool(row[6])
            slot["estimated_origin"] = slot["estimated_origin"] or bool(row[7])
            slot["price_missing"] = slot["price_missing"] or bool(row[8])
        # Item types that exist only as drift (ESI has them, the ledger does not).
        for type_id, drift in drift_rows.items():
            if (type_id, "item") not in grouped and abs(_num(drift[3])) > _EPS:
                grouped[(type_id, "item")] = {
                    "type_id": type_id,
                    "unit": "item",
                    "quantity": 0.0,
                    "total": 0.0,
                    "origin_mix": {"bought": 0.0, "produced": 0.0, "opening": 0.0, "unknown": 0.0},
                    "assumed_me": False,
                    "assumed_decryptor": False,
                    "estimated_origin": False,
                    "price_missing": False,
                }
        names = _names({key[0] for key in grouped})
    rows = []
    for key in sorted(grouped):
        slot = grouped[key]
        type_id, unit = key
        drift = drift_rows.get(type_id) if unit == "item" else None
        qty = slot["quantity"]
        rows.append({
            "type_id": type_id,
            "type_name": names.get(type_id),
            "unit": unit,
            "quantity": qty,
            "cost_per_unit": (slot["total"] / qty) if qty else None,
            "total": slot["total"],
            "origin_mix": slot["origin_mix"],
            "esi_stock": None if drift is None else _num(drift[2]),
            "drift": None if drift is None else _num(drift[3]),
            "flags": _flags(
                estimated=slot["estimated_origin"] or slot["price_missing"],
                price_missing=slot["price_missing"],
                assumed_me=slot["assumed_me"],
                assumed_decryptor=slot["assumed_decryptor"],
            ),
        })
    return {"needs_rebuild": False, "rows": rows}


def _warning_kind(text: str) -> str:
    if text.startswith("no wallet archive"):
        return "no_archive"
    head, sep, _rest = text.partition(":")
    return head.strip() if sep else "other"


def warnings() -> dict:
    """Ledger warnings from the latest rebuild, plus archive gaps and
    unresolved regions read from the archive (D11: only owners still shared
    with Production). Not filtered by the overview period."""
    groups: dict[str, list] = {kind: [] for kind in SECTION_9A_KINDS}
    with storage.batch_session():
        run, _t0, changed = _run_state()
        hidden = run is None or changed
        texts = _warnings(run[3]) if not hidden else []
        derived_drift = [] if hidden else db.list_inventory_drift()
        derived_jobs = [] if hidden else db.list_job_records()
        derived_fees = [] if hidden else db.list_fee_lines("without_sale")
        for type_id, ledger_qty, esi_qty, drift in derived_drift:
            if abs(_num(drift)) <= _EPS:
                continue
            groups["inventory_drift"].append({
                "type_id": int(type_id),
                "ledger_qty": _num(ledger_qty),
                "esi_qty": _num(esi_qty),
                "drift": _num(drift),
            })
        for row in derived_jobs:
            if row[20]:
                groups["assumed_me"].append({"job_ref": str(row[0]), "job_id": int(row[1])})
            if row[21]:
                groups["assumed_decryptor"].append({"job_ref": str(row[0]), "job_id": int(row[1])})
        for owner_type, owner_id, fee_date, amount, sale_ref in derived_fees:
            groups["unmatched_broker_fee"].append({
                "owner_type": owner_type,
                "owner_id": int(owner_id),
                "fee_date": _iso(fee_date),
                "amount": _num(amount),
                "sale_ref": sale_ref,
            })
        shared = {
            kind: set(access.shared_owners(kind))
            for kind in ("wallet", "industry_jobs", "market_orders", "contracts")
        }
        for owner_type, owner_id, data_kind, gap_start, gap_end in db.list_all_archive_gaps():
            share_kind = _GAP_SHARE_KIND.get(str(data_kind))
            if share_kind is None or (owner_type, int(owner_id)) not in shared.get(share_kind, ()):
                continue
            groups["archive_gap"].append({
                "owner_type": owner_type,
                "owner_id": int(owner_id),
                "data_kind": str(data_kind),
                "gap_start": _iso(gap_start),
                "gap_end": _iso(gap_end),
            })
        for location_id, count in db.unresolved_regions(list(shared["wallet"])):
            groups["unresolved_region"].append({
                "location_id": int(location_id),
                "count": int(count),
            })
        outside = [] if hidden else db.realized_warnings_by_type(
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(9999, 1, 1, tzinfo=timezone.utc),
        )
    seen_outside = set()
    for type_id, text in outside:
        if str(text) != "outside_home_structure" or int(type_id) in seen_outside:
            continue
        seen_outside.add(int(type_id))
        groups["outside_home_structure"].append({"type_id": int(type_id), "message": str(text)})

    # These kinds are built from tables above. Matching warning strings would
    # duplicate them. trading_overlap and negative_opening exist only as text.
    consumed_prefixes = {
        "inventory_drift", "assumed_me", "assumed_decryptor", "archive_gap",
        "unresolved_region", "outside_home_structure", "unmatched_broker_fee",
        "fees_without_sale", "fees_on_other_trading",
    }
    extra: dict[str, list] = defaultdict(list)
    for text in texts:
        kind = _warning_kind(text)
        if kind in ("trading_overlap", "negative_opening"):
            groups[kind].append({"message": text})
        elif kind in consumed_prefixes:
            continue
        else:
            extra[kind].append({"message": text})

    ordered = [{"kind": kind, "count": len(groups[kind]), "items": groups[kind]} for kind in SECTION_9A_KINDS]
    for kind in sorted(extra):
        ordered.append({"kind": kind, "count": len(extra[kind]), "items": extra[kind]})
    job_map = {
        str(row[0]): {
            "type_id": None if row[6] is None else int(row[6]),
            "activity": display_names.activity_label(row[4]),
            "owner_id": int(row[3]),
        }
        for row in derived_jobs
    }
    with storage.batch_session():
        display_names.label_warnings(ordered, job_map)
    return {"needs_rebuild": hidden, "groups": ordered}


def opening() -> dict:
    """Opening inventory as the latest rebuild valued it, with the frozen
    date and any opening-cost override (section 9a Ledger tab)."""
    with storage.batch_session():
        run, t0, changed = _run_state()
        if run is None or changed:
            return {"cutover_t0": _iso(t0), "needs_rebuild": True, "rows": []}
        rows = db.list_opening_lines()
        overrides = db.list_opening_cost_overrides()
        names = _names({int(r[0]) for r in rows})
        valued_as = db.price_sources_on([int(r[0]) for r in rows], t0.date()) if t0 is not None else {}
    out = []
    for type_id, raw, qty, unit_cost, source, reference, frozen_at in rows:
        tid = int(type_id)
        override = overrides.get(tid)
        out.append({
            "type_id": tid,
            "type_name": names.get(tid),
            "raw_quantity": _num(raw),
            "quantity": _num(qty),
            "reference_price": None if reference is None else float(reference),
            "unit_cost": _num(unit_cost),
            "price_source": str(source),
            # buy_home / buy_hub / build / forge_average: how the T0 value was found.
            "valued_as": valued_as.get(tid),
            "override_unit_cost": None if override is None else float(override),
            "frozen_at": _iso(frozen_at),
            "flags": _flags(
                estimated=str(source) in ("reference", "missing"),
                price_missing=str(source) == "missing",
            ),
        })
    return {"cutover_t0": _iso(t0), "needs_rebuild": False, "rows": out}


def ledger_entries() -> dict:
    """Manual entries (cost, revenue, opening_cost overrides), newest first."""
    with storage.batch_session():
        rows = db.list_manual_entries()
        names = _names({int(r[2]) for r in rows if r[2] is not None})
    return {
        "rows": [
            {
                "entry_id": int(r[0]),
                "entry_kind": str(r[1]),
                "type_id": None if r[2] is None else int(r[2]),
                "type_name": None if r[2] is None else names.get(int(r[2])),
                "quantity": None if r[3] is None else float(r[3]),
                "amount": _num(r[4]),
                "entry_date": _iso(r[5]),
                "note": r[6],
                "created_at": _iso(r[7]),
            }
            for r in rows
        ],
    }


def courier_comparison() -> dict:
    """Finished courier contracts from The Forge to the home structure,
    per calendar month. Information only: costing still uses the configured
    rate (D9). An unset home structure returns no months."""
    from ..config import PRODUCTION_CONFIG
    from ...config import TRADING_CONFIG
    from .ingest import _freight_rate_for_forge

    home = PRODUCTION_CONFIG.home_location_id or TRADING_CONFIG.structure_id
    rate = float(_freight_rate_for_forge(PRODUCTION_CONFIG))
    if not home:
        return {"home_location_id": None, "configured_rate_per_m3": rate, "months": []}
    with storage.batch_session():
        rows = db.list_forge_home_couriers(access.shared_owners("contracts"), int(home))
    buckets: dict[str, dict] = {}
    for _cid, completed, volume, reward in rows:
        when = completed
        if isinstance(when, datetime) and when.tzinfo is not None:
            when = when.astimezone(timezone.utc)
        month = when.strftime("%Y-%m")
        slot = buckets.setdefault(month, {"count": 0, "volume_m3": 0.0, "reward": 0.0})
        slot["count"] += 1
        slot["volume_m3"] += float(volume or 0)
        slot["reward"] += float(reward or 0)
    months = []
    for month in sorted(buckets):
        slot = buckets[month]
        vol = slot["volume_m3"]
        months.append({
            "month": month,
            "count": slot["count"],
            "volume_m3": vol,
            "reward": slot["reward"],
            "paid_isk_per_m3": (slot["reward"] / vol) if vol > _EPS else None,
            "configured_rate_per_m3": rate,
        })
    return {
        "home_location_id": int(home),
        "configured_rate_per_m3": rate,
        "months": months,
    }


_ARCHIVE_KINDS = ("wallet", "industry_jobs", "market_orders", "contracts")


def archive_status() -> dict:
    """One row per owner that is shared with Production or has archived
    rows. `hidden_kinds`: archived but no longer shared (D11, rows kept)."""
    with storage.batch_session():
        shared = {kind: set(access.shared_owners(kind)) for kind in _ARCHIVE_KINDS}
        sync_rows = db.list_ledger_sync_state()
        gaps = db.archive_gap_counts()
    owners: dict[tuple, dict] = {}

    def slot(owner_type: str, owner_id: int) -> dict:
        key = (owner_type, int(owner_id))
        if key not in owners:
            owners[key] = {
                "owner_type": owner_type,
                "owner_id": int(owner_id),
                "shared": {kind: key in shared[kind] for kind in _ARCHIVE_KINDS},
                "last_sync": {},
                "gaps": {},
                "archived_kinds": set(),
            }
        return owners[key]

    for keys in shared.values():
        for owner_type, owner_id in keys:
            slot(owner_type, owner_id)
    for owner_type, owner_id, data_kind, synced in sync_rows:
        entry = slot(owner_type, owner_id)
        previous = entry["last_sync"].get(str(data_kind))
        if previous is None or (synced is not None and synced > previous):
            entry["last_sync"][str(data_kind)] = synced
        share_kind = _GAP_SHARE_KIND.get(str(data_kind))
        if share_kind is not None:
            entry["archived_kinds"].add(share_kind)
    for (owner_type, owner_id, data_kind), count in gaps.items():
        slot(owner_type, owner_id)["gaps"][str(data_kind)] = count
    owner_names = display_names.owner_names(owner_id for _owner_type, owner_id in owners)
    rows = []
    for key in sorted(owners):
        entry = owners[key]
        archived = entry.pop("archived_kinds")
        entry["hidden_kinds"] = sorted(k for k in archived if not entry["shared"][k])
        entry["last_sync"] = {k: _iso(v) for k, v in sorted(entry["last_sync"].items())}
        entry["owner_name"] = owner_names.get(int(entry["owner_id"]))
        rows.append(entry)
    return {"rows": rows}

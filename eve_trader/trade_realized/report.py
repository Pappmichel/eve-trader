"""Trading realized-profit report.

Reads derived tables. It does not run costing. Profit, cost and margin
are computed here; the page only displays them. A missing value stays
missing.

U3a: the run fingerprint is the union of realized-tool shares. This page
then keeps channel `trading`. Market sales match a wallet shared with
Trading. Contract sales of non-Production types use the same wallet list,
because Trading has no contracts share of its own.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import storage
from ..realized import access, db, names as display_names
from ..realized.costing import ESTIMATED_COVERAGE_THRESHOLD, CostComponents

CHANNEL = "trading"


def _scope() -> dict:
    owners = list(access.shared_owners_for("wallet", "trading"))
    return {
        "channel": CHANNEL,
        "wallet_owners": owners,
        "contract_owners": owners,
    }


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
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
    cov = components.coverage()
    if cov is None:
        cov = 0.0 if price_missing else 1.0
    estimated = bool(price_missing) or cov < ESTIMATED_COVERAGE_THRESHOLD
    return cov, estimated


def _margin(profit: float, revenue: float) -> Optional[float]:
    if not revenue:
        return None
    return profit / revenue


def _flags(*, estimated: bool, price_missing: bool, coverage: Optional[float] = None,
           tax_missing: bool = False) -> dict:
    return {
        "estimated": bool(estimated),
        "price_missing": bool(price_missing),
        "assumed_me": False,
        "assumed_decryptor": False,
        "coverage": coverage,
        "tax_missing": bool(tax_missing),
        "detail_missing": False,
        "no_structure_bonus": None,
    }


def _names(type_ids: set[int]) -> dict[int, str]:
    if not type_ids:
        return {}
    return storage.sde_type_names(type_ids)


def _run_state() -> tuple[Optional[tuple], Optional[datetime], bool]:
    run = db.latest_ledger_run()
    if run is None:
        return None, None, False
    t0 = None if run[2] is None else _as_utc(run[2])
    changed = run[4] is None or run[4] != access.owner_scope_hash()
    return run, t0, changed


def _status(run, t0, archive_since, wallet_sync, results_ever: int, *,
            sharing_changed: bool, last_sync: Optional[datetime]) -> dict:
    stale = False
    if run is not None and last_sync is not None and run[1] is not None:
        stale = _as_utc(last_sync) > _as_utc(run[1])
    if run is None:
        hint = "Recompute to build realized profit from the archive."
        empty = True
    elif sharing_changed:
        hint = "Sharing changed since the last recompute. Recompute to update."
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
        "last_jobs_sync": None,
        "run_id": None if run is None else int(run[0]),
    }


def _sync_times() -> tuple[Optional[datetime], Optional[datetime], Optional[datetime]]:
    owners = set(access.shared_owners_for("wallet", "trading"))
    wallet_sync = None
    for owner_type, owner_id, kind, synced in db.list_ledger_sync_state():
        if str(kind) not in ("wallet_journal", "wallet_transactions"):
            continue
        if (owner_type, int(owner_id)) not in owners or synced is None:
            continue
        if wallet_sync is None or synced > wallet_sync:
            wallet_sync = synced
    archive_since = db.earliest_archived_transaction(list(owners))
    return archive_since, wallet_sync, wallet_sync


def _window(period_start: Optional[datetime], period_end: datetime, since_t0: bool,
            t0: Optional[datetime]) -> Optional[tuple[datetime, datetime]]:
    if since_t0:
        if t0 is None:
            return None
        return _as_utc(t0), period_end
    assert period_start is not None
    return period_start, period_end


def _period_dict(start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    return {
        "start": _iso(start),
        "end": _iso(end),
        "since_t0": since_t0,
        "days": days,
    }


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
    labels = (
        ("materials_bought", "Materials bought"),
        ("materials_estimated", "Opening / unknown stock (planner value)"),
        ("freight", "Freight (configured rate)"),
        ("job", "Job costs"),
        ("invention", "Invention"),
        ("broker_fees", "Broker fees"),
        ("sales_tax", "Sales tax"),
    )
    return [{"key": key, "label": label, "amount": amounts[key]} for key, label in labels]


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
        "sale_count": 0,
        "price_missing": False,
        "tax_missing_sales": 0,
        "cost_structure": _cost_structure(zero, 0.0),
        "by_day": [],
        "flags": _flags(estimated=False, price_missing=False, coverage=None),
    }


def summary(*, start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    with storage.batch_session():
        run, t0, changed = _run_state()
        archive_since, wallet_sync, any_sync = _sync_times()
        scope = _scope()
        results_ever = db.count_realized_results(**scope) if run is not None else 0
        status = _status(
            run, t0, archive_since, wallet_sync, results_ever,
            sharing_changed=changed, last_sync=any_sync,
        )
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        period = _period_dict(None if window is None else window[0], end, since_t0, days)
        if window is None:
            return _empty_summary(period, status)
        start_at, end_at = window
        totals = db.realized_totals(start_at, end_at, **scope)
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
        by_day = [
            {"date": _iso(row[0]), "revenue": _num(row[1]), "cost": _num(row[2]), "profit": _num(row[3])}
            for row in db.realized_by_day(start_at, end_at, **scope)
        ]
    return {
        "period": period,
        "status": status,
        "revenue": revenue,
        "cost_of_goods_sold": cost,
        "realized_profit": profit,
        "margin": _margin(profit, revenue),
        "coverage": cov,
        "estimated": estimated,
        "units_sold": _num(totals[3]),
        "sale_count": sales_count,
        "price_missing": price_missing,
        "tax_missing_sales": int(totals[11]) if sales_count else 0,
        "cost_structure": _cost_structure(components, tax),
        "by_day": by_day,
        "flags": _flags(
            estimated=estimated, price_missing=price_missing, coverage=cov,
            tax_missing=int(totals[11]) > 0,
        ),
    }


def _item_row(type_id: int, totals: tuple, warnings: list[str], names: dict) -> dict:
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
    return {
        "type_id": int(type_id),
        "type_name": names.get(int(type_id)),
        "units_sold": qty,
        "avg_buy_landed": (cost / qty) if qty else None,
        "avg_net_sell": (revenue / qty) if qty else None,
        "profit": profit,
        "margin": _margin(profit, revenue),
        "coverage": cov,
        "warnings": sorted(set(warnings)),
        "flags": _flags(
            estimated=estimated, price_missing=price_missing, coverage=cov,
            tax_missing="tax_missing" in warnings,
        ),
    }


def items(*, start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    with storage.batch_session():
        run, t0, changed = _run_state()
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        period = _period_dict(None if window is None else window[0], end, since_t0, days)
        if window is None:
            return {"period": period, "rows": [], "needs_rebuild": run is None or changed}
        start_at, end_at = window
        scope = _scope()
        rows = db.realized_by_type(start_at, end_at, **scope)
        warn: dict[int, list[str]] = {}
        for type_id, text in db.realized_warnings_by_type(start_at, end_at, **scope):
            warn.setdefault(int(type_id), []).append(str(text))
        names = _names({int(row[0]) for row in rows})
    built = [
        _item_row(int(row[0]), row, warn.get(int(row[0]), []), names)
        for row in rows
    ]
    built.sort(key=lambda row: row["profit"], reverse=True)
    return {"period": period, "needs_rebuild": False, "rows": built}


def _sale_dict(row, names: dict, locations: dict[int, str]) -> dict:
    components = _components_from(row, 15)
    price_missing = bool(row[12])
    estimated = bool(row[13])
    qty = _num(row[2])
    revenue = _num(row[3])
    warnings = _warnings(row[14])
    cov = _num(row[6])
    location_id = None if row[11] is None else int(row[11])
    return {
        "sale_ref": str(row[0]),
        "type_id": int(row[1]),
        "type_name": names.get(int(row[1])),
        "quantity": qty,
        "revenue": revenue,
        "cost": _num(row[4]),
        "profit": _num(row[5]),
        "margin": _margin(_num(row[5]), revenue),
        "coverage": cov,
        "sale_date": _iso(row[7]),
        "gross": _num(row[8]),
        "sales_tax": None if row[9] is None else float(row[9]),
        "broker_fee": _num(row[10]),
        "location_id": location_id,
        "location_name": None if location_id is None else locations.get(location_id),
        "net_price": (revenue / qty) if qty else None,
        "warnings": warnings,
        "cost_structure": _cost_structure(components, 0.0 if row[9] is None else float(row[9])),
        "flags": _flags(
            estimated=estimated, price_missing=price_missing, coverage=cov,
            tax_missing="tax_missing" in warnings,
        ),
        "source": None if len(row) <= 21 or row[21] is None else str(row[21]),
    }


def item(*, type_id: int, start: Optional[datetime], end: datetime,
         since_t0: bool, days: Optional[int]) -> dict:
    listing = items(start=start, end=end, since_t0=since_t0, days=days)
    match = next((row for row in listing["rows"] if row["type_id"] == int(type_id)), None)
    with storage.batch_session():
        run, t0, changed = _run_state()
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        sales = [] if window is None else db.list_sales_for_type(
            int(type_id), window[0], window[1], **_scope(),
        )
        names = _names({int(type_id)} | {int(row[1]) for row in sales})
        locations = display_names.location_names(
            int(row[11]) for row in sales if row[11] is not None
        )
    if match is None:
        match = {
            "type_id": int(type_id),
            "type_name": names.get(int(type_id)),
            "units_sold": 0.0,
            "avg_buy_landed": None,
            "avg_net_sell": None,
            "profit": 0.0,
            "margin": None,
            "coverage": None,
            "warnings": [],
            "flags": _flags(estimated=False, price_missing=False, coverage=None),
        }
    return {
        "period": listing["period"],
        "needs_rebuild": listing["needs_rebuild"],
        "item": match,
        "sales": [_sale_dict(row, names, locations) for row in sales],
    }


def _lot(row, locations: dict[int, str]) -> dict:
    location_id = None if row[21] is None else int(row[21])
    return {
        "quantity": _num(row[5]),
        "origin": str(row[6]),
        "layer_date": _iso(row[19]),
        "location_id": location_id,
        "location_name": None if location_id is None else locations.get(location_id),
        "unit_price": None if row[22] is None else float(row[22]),
        "freight": _num(row[10]),
        "fees": _num(row[13]),
        "unit_cost": _num(row[14]),
    }


def sale(sale_ref: str) -> Optional[dict]:
    with storage.batch_session():
        run, _t0, changed = _run_state()
        if run is None or changed:
            return None
        row = db.get_realized_sale(sale_ref, **_scope())
        if row is None:
            return None
        draws = db.list_draws("sale", [sale_ref])
        names = _names({int(row[1])})
        location_ids = [row[11]] + [draw[21] for draw in draws]
        locations = display_names.location_names(
            int(value) for value in location_ids if value is not None
        )
    return {
        "sale": _sale_dict(row, names, locations),
        "lots": [_lot(draw, locations) for draw in draws],
    }


def _production_type_ids() -> set[int]:
    ids = {int(row[0]) for row in storage.load_stock_targets()}
    with storage.connect() as conn:
        products = conn.execute(
            "SELECT DISTINCT product_type_id FROM prod_job_records "
            "WHERE product_type_id IS NOT NULL",
        ).fetchall()
    ids.update(int(row[0]) for row in products)
    for _oid, _note, type_id, _name, _qty in storage.list_integrated_special_order_items():
        ids.add(int(type_id))
    return ids


def inventory() -> dict:
    """Unsold Trading goods in the global pool. The station pool is Station
    Trading's open position and stays off this list."""
    from ..station_trading.config import STATION_TRADING_CONFIG
    station_pool = str(STATION_TRADING_CONFIG.station_id)
    with storage.batch_session():
        run, _t0, changed = _run_state()
        if run is None or changed:
            return {"needs_rebuild": True, "rows": []}
        excluded = _production_type_ids()
        grouped: dict[tuple, dict] = {}
        for row in db.list_remaining_layers():
            type_id = int(row[0])
            if str(row[1]) != "item" or type_id in excluded:
                continue
            pool = str(row[9]) if len(row) > 9 and row[9] is not None else "global"
            if pool == station_pool:
                continue
            key = (type_id, pool)
            slot = grouped.get(key)
            if slot is None:
                slot = {
                    "type_id": type_id,
                    "pool": pool,
                    "quantity": 0.0,
                    "total": 0.0,
                    "price_missing": False,
                    "estimated": False,
                }
                grouped[key] = slot
            qty = _num(row[2])
            slot["quantity"] += qty
            slot["total"] += qty * _num(row[3])
            slot["price_missing"] = slot["price_missing"] or bool(row[8])
            slot["estimated"] = slot["estimated"] or bool(row[7]) or bool(row[8])
        names = _names({key[0] for key in grouped})
    rows = []
    for key in sorted(grouped):
        slot = grouped[key]
        qty = slot["quantity"]
        rows.append({
            "type_id": slot["type_id"],
            "type_name": names.get(slot["type_id"]),
            "pool": slot["pool"],
            "quantity": qty,
            "cost_per_unit": (slot["total"] / qty) if qty else None,
            "total": slot["total"],
            "flags": _flags(
                estimated=slot["estimated"], price_missing=slot["price_missing"],
            ),
        })
    return {"needs_rebuild": False, "rows": rows}


def warnings() -> dict:
    with storage.batch_session():
        run, _t0, changed = _run_state()
        if run is None or changed:
            return {"needs_rebuild": True, "rows": []}
        raw = db.realized_warnings_by_type(
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(9999, 1, 1, tzinfo=timezone.utc),
            **_scope(),
        )
        names = _names({int(type_id) for type_id, _text in raw})
    seen = set()
    rows = []
    for type_id, text in raw:
        key = (int(type_id), str(text))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "type_id": int(type_id),
            "type_name": names.get(int(type_id)),
            "message": str(text),
        })
    return {"needs_rebuild": False, "rows": rows}


def _daily_volatility(profits: list[float]) -> Optional[float]:
    """Population standard deviation. One day has no spread."""
    if len(profits) < 2:
        return None
    mean = sum(profits) / len(profits)
    variance = sum((value - mean) ** 2 for value in profits) / len(profits)
    return math.sqrt(variance)


def portfolio_figures() -> dict:
    """Last 30 days of the Trading channel, for Portfolio's combined value.

    Margin is profit divided by cost of goods (what was paid, including
    freight and fees). Volatility is the population standard deviation of
    the days that had a sale. A run that still needs a recompute contributes
    zeros, the same as a tenant with no trades.
    """
    now = datetime.now(timezone.utc)
    data = summary(start=now - timedelta(days=30), end=now, since_t0=False, days=30)
    empty = data["status"]["empty"] or data["status"]["needs_rebuild"]
    if empty:
        return {
            "trading_realized_profit": 0.0,
            "trading_average_margin": 0.0,
            "trading_daily_profit_volatility": None,
            "trading_trade_count": 0,
        }
    cost = data["cost_of_goods_sold"]
    return {
        "trading_realized_profit": data["realized_profit"],
        "trading_average_margin": (data["realized_profit"] / cost) if cost else 0.0,
        "trading_daily_profit_volatility": _daily_volatility(
            [row["profit"] for row in data["by_day"]]
        ),
        "trading_trade_count": data["sale_count"],
    }


def compare_trading(days: int) -> dict:
    """Old `realized_trades` totals beside the Trading channel for one window.

    Read-only. The latest reconciliation run is filtered to sells in the
    window; the ledger side is the Trading report for the same window.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=int(days))
    old_by_type = _old_realized_trades(start)
    listing = items(start=start, end=now, since_t0=False, days=int(days))
    new_by_type = {
        int(row["type_id"]): row for row in listing["rows"]
    }
    type_ids = sorted(set(old_by_type) | set(new_by_type))
    names = _names(set(type_ids))
    rows = []
    old_profit = old_units = new_profit = new_units = 0.0
    for type_id in type_ids:
        old = old_by_type.get(type_id, {"units": 0.0, "profit": 0.0, "item": None})
        new = new_by_type.get(type_id)
        old_profit += old["profit"]
        old_units += old["units"]
        if new is not None:
            new_profit += new["profit"]
            new_units += new["units_sold"]
        rows.append({
            "type_id": type_id,
            "item": (new or {}).get("type_name") or old["item"] or names.get(type_id) or str(type_id),
            "old_units": old["units"],
            "old_profit": old["profit"],
            "new_units": 0.0 if new is None else new["units_sold"],
            "new_profit": 0.0 if new is None else new["profit"],
        })
    return {
        "days": int(days),
        "needs_rebuild": bool(listing["needs_rebuild"]),
        "old_units": old_units,
        "old_profit": old_profit,
        "new_units": new_units,
        "new_profit": new_profit,
        "rows": rows,
    }


def _old_realized_trades(start: datetime) -> dict[int, dict]:
    frame = storage.read_table("realized_trades")
    if frame.empty:
        return {}
    latest_run = frame["run_ts"].max()
    latest = frame[frame["run_ts"] == latest_run]
    grouped: dict[int, dict] = {}
    for record in latest.to_dict("records"):
        sold = _parse_time(record.get("sell_date"))
        if sold is None or sold < start:
            continue
        type_id = int(record["type_id"])
        slot = grouped.get(type_id)
        if slot is None:
            slot = {"units": 0.0, "profit": 0.0, "item": record.get("item")}
            grouped[type_id] = slot
        slot["units"] += float(record.get("matched_qty") or 0)
        slot["profit"] += float(record.get("realized_profit") or 0)
    return grouped


def _parse_time(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

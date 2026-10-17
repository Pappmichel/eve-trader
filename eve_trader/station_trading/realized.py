"""Station Trading realized-profit report.

Reads the station_trading channel of the shared ledger. Profit, cost,
spread and turnover are computed here; the page only displays them.

U3a: the run fingerprint is the union of realized-tool shares. This page
then keeps channel `station_trading` and owners whose wallet is shared
with Station Trading. Contract sales use that same wallet list, because
Station Trading has no contracts share of its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import storage
from ..actions import ActionError
from ..realized import access, db, names as display_names
from ..realized.costing import ESTIMATED_COVERAGE_THRESHOLD, CostComponents
from .config import STATION_TRADING_CONFIG

CHANNEL = "station_trading"
_SECONDS_PER_DAY = 86400.0


def _scope() -> dict:
    owners = list(access.shared_owners_for("wallet", "station_trading"))
    return {
        "channel": CHANNEL,
        "wallet_owners": owners,
        "contract_owners": owners,
    }


def _station_pool() -> str:
    station_id = STATION_TRADING_CONFIG.station_id
    if station_id is None:
        return ""
    return str(int(station_id))


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


def _per_unit(amount: float, quantity: float) -> Optional[float]:
    if not quantity:
        return None
    return amount / quantity


def _spread(avg_sell: Optional[float], avg_buy: Optional[float]) -> Optional[float]:
    if avg_sell is None or avg_buy is None:
        return None
    return avg_sell - avg_buy


def _turnover_days(quantity: float, weighted_seconds: float) -> Optional[float]:
    if not quantity:
        return None
    return weighted_seconds / quantity / _SECONDS_PER_DAY


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


def _sync_times() -> tuple[Optional[datetime], Optional[datetime]]:
    owners = set(access.shared_owners_for("wallet", "station_trading"))
    wallet_sync = None
    for owner_type, owner_id, kind, synced in db.list_ledger_sync_state():
        if str(kind) not in ("wallet_journal", "wallet_transactions"):
            continue
        if (owner_type, int(owner_id)) not in owners or synced is None:
            continue
        if wallet_sync is None or synced > wallet_sync:
            wallet_sync = synced
    return db.earliest_archived_transaction(list(owners)), wallet_sync


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


def _open_position_rows() -> list[dict]:
    """Leftover layers in the station pool, at cost. Current stock, not a period."""
    pool = _station_pool()
    grouped: dict[int, dict] = {}
    if pool:
        for row in db.list_remaining_layers():
            if str(row[1]) != "item":
                continue
            row_pool = str(row[9]) if len(row) > 9 and row[9] is not None else "global"
            if row_pool != pool:
                continue
            type_id = int(row[0])
            slot = grouped.get(type_id)
            if slot is None:
                slot = {
                    "type_id": type_id,
                    "quantity": 0.0,
                    "total": 0.0,
                    "price_missing": False,
                    "estimated": False,
                }
                grouped[type_id] = slot
            qty = _num(row[2])
            slot["quantity"] += qty
            slot["total"] += qty * _num(row[3])
            slot["price_missing"] = slot["price_missing"] or bool(row[8])
            slot["estimated"] = slot["estimated"] or bool(row[7]) or bool(row[8])
    names = _names(set(grouped))
    rows = []
    for type_id in sorted(grouped):
        slot = grouped[type_id]
        qty = slot["quantity"]
        rows.append({
            "type_id": type_id,
            "type_name": names.get(type_id),
            "quantity": qty,
            "cost_per_unit": (slot["total"] / qty) if qty else None,
            "total": slot["total"],
            "flags": _flags(estimated=slot["estimated"], price_missing=slot["price_missing"]),
        })
    return rows


def _empty_summary(period: dict, status: dict) -> dict:
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
        "avg_buy": None,
        "avg_sell_net": None,
        "spread": None,
        "turnover_days": None,
        "price_missing": False,
        "tax_missing_sales": 0,
        "fees_by_day": [],
        "by_day": [],
        "open_position_cost": None,
        "open_position_units": None,
        "flags": _flags(estimated=False, price_missing=False, coverage=None),
    }


def _fee_rows(start_at: datetime, end_at: datetime, scope: dict) -> list[dict]:
    rows = []
    for day, broker, tax, missing in db.fees_by_day(start_at, end_at, **scope):
        tax_value = None if tax is None else float(tax)
        rows.append({
            "date": _iso(day),
            "broker_fee": float(broker),
            "sales_tax": tax_value,
            "total": float(broker) + (0.0 if tax_value is None else tax_value),
            "tax_missing_sales": int(missing),
        })
    return rows


def _turnover_map(start_at: datetime, end_at: datetime, scope: dict) -> dict[int, tuple[float, float]]:
    out = {}
    for type_id, quantity, seconds in db.turnover_weighted_by_type(start_at, end_at, **scope):
        out[int(type_id)] = (_num(quantity), _num(seconds))
    return out


def summary(*, start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    with storage.batch_session():
        run, t0, changed = _run_state()
        archive_since, wallet_sync = _sync_times()
        scope = _scope()
        results_ever = db.count_realized_results(**scope) if run is not None else 0
        status = _status(
            run, t0, archive_since, wallet_sync, results_ever,
            sharing_changed=changed, last_sync=wallet_sync,
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
        units = _num(totals[3])
        avg_buy = _per_unit(cost, units)
        avg_sell = _per_unit(revenue, units)
        turn = _turnover_map(start_at, end_at, scope)
        dated_qty = sum(pair[0] for pair in turn.values())
        dated_seconds = sum(pair[1] for pair in turn.values())
        position = _open_position_rows()
        by_day = [
            {"date": _iso(row[0]), "revenue": _num(row[1]), "cost": _num(row[2]), "profit": _num(row[3])}
            for row in db.realized_by_day(start_at, end_at, **scope)
        ]
        fees = _fee_rows(start_at, end_at, scope)
    return {
        "period": period,
        "status": status,
        "revenue": revenue,
        "cost_of_goods_sold": cost,
        "realized_profit": profit,
        "margin": _margin(profit, revenue),
        "coverage": cov,
        "estimated": estimated,
        "units_sold": units,
        "sale_count": sales_count,
        "avg_buy": avg_buy,
        "avg_sell_net": avg_sell,
        "spread": _spread(avg_sell, avg_buy),
        "turnover_days": _turnover_days(dated_qty, dated_seconds),
        "price_missing": price_missing,
        "tax_missing_sales": int(totals[11]) if sales_count else 0,
        "fees_by_day": fees,
        "by_day": by_day,
        "open_position_cost": sum(row["total"] for row in position),
        "open_position_units": sum(row["quantity"] for row in position),
        "flags": _flags(
            estimated=estimated, price_missing=price_missing, coverage=cov,
            tax_missing=int(totals[11]) > 0,
        ),
    }


def _item_row(type_id: int, totals: tuple, warnings: list[str], names: dict,
              turnover: tuple[float, float]) -> dict:
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
    avg_buy = _per_unit(cost, qty)
    avg_sell = _per_unit(revenue, qty)
    return {
        "type_id": int(type_id),
        "type_name": names.get(int(type_id)),
        "units_sold": qty,
        "avg_buy": avg_buy,
        "avg_sell_net": avg_sell,
        "spread": _spread(avg_sell, avg_buy),
        "profit": profit,
        "margin": _margin(profit, revenue),
        "turnover_days": _turnover_days(turnover[0], turnover[1]),
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
        turn = _turnover_map(start_at, end_at, scope)
        names = _names({int(row[0]) for row in rows})
    built = [
        _item_row(int(row[0]), row, warn.get(int(row[0]), []), names, turn.get(int(row[0]), (0.0, 0.0)))
        for row in rows
    ]
    built.sort(key=lambda row: row["profit"], reverse=True)
    return {"period": period, "needs_rebuild": False, "rows": built}


def _sale_dict(row, names: dict, locations: dict[int, str]) -> dict:
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
            "avg_buy": None,
            "avg_sell_net": None,
            "spread": None,
            "profit": 0.0,
            "margin": None,
            "turnover_days": None,
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


def position() -> dict:
    with storage.batch_session():
        run, _t0, changed = _run_state()
        if run is None or changed:
            return {"needs_rebuild": True, "rows": [], "cost": None, "units": None}
        rows = _open_position_rows()
    return {
        "needs_rebuild": False,
        "rows": rows,
        "cost": sum(row["total"] for row in rows),
        "units": sum(row["quantity"] for row in rows),
    }


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


def _parse_time(value: str, name: str) -> datetime:
    text = (value or "").strip()
    if not text:
        raise ActionError(f"{name} is required.")
    try:
        parsed = datetime.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(
            text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActionError(f"{name} must be an ISO date or datetime.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _period(days: int | None, start: str | None, end: str | None, since_t0: bool) -> dict:
    if since_t0 and (days is not None or start or end):
        raise ActionError("since_t0 cannot be combined with days or a custom range.")
    now = datetime.now(timezone.utc)
    if since_t0:
        return {"start": None, "end": now, "since_t0": True, "days": None}
    if start or end:
        if not start or not end:
            raise ActionError("A custom range needs both start and end.")
        start_at = _parse_time(start, "start")
        end_at = _parse_time(end, "end")
        if end_at <= start_at:
            raise ActionError("end must be after start.")
        return {"start": start_at, "end": end_at, "since_t0": False, "days": None}
    window = 30 if days is None else int(days)
    if window < 1 or window > 3660:
        raise ActionError("days must be between 1 and 3660.")
    return {"start": now - timedelta(days=window), "end": now, "since_t0": False, "days": window}


def do_get_station_realized_summary(days: int | None = None, start: str | None = None,
                                    end: str | None = None, since_t0: bool = False) -> dict:
    return summary(**_period(days, start, end, since_t0))


def do_get_station_realized_items(days: int | None = None, start: str | None = None,
                                  end: str | None = None, since_t0: bool = False) -> dict:
    return items(**_period(days, start, end, since_t0))


def do_get_station_realized_item(type_id: int, days: int | None = None, start: str | None = None,
                                 end: str | None = None, since_t0: bool = False) -> dict:
    if int(type_id) <= 0:
        raise ActionError("type_id must be a positive item id.")
    return item(type_id=int(type_id), **_period(days, start, end, since_t0))


def do_get_station_realized_sale(sale_ref: str) -> dict:
    ref = (sale_ref or "").strip()
    if not ref or len(ref) > 200:
        raise ActionError("sale_ref is required.")
    found = sale(ref)
    if found is None:
        raise ActionError(f"No realized sale {ref}.")
    return found


def do_get_station_realized_position() -> dict:
    return position()


def do_get_station_realized_warnings() -> dict:
    return warnings()


def do_start_station_realized_rebuild() -> dict:
    """The same background rebuild Production uses. One ledger, one job."""
    from .. import pipeline_runner
    return pipeline_runner.start_rebuild_realized()


def do_station_realized_rebuild_status() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.job_status(
        pipeline_runner.TOOL_PRODUCTION, pipeline_runner.JOB_REBUILD_REALIZED,
    )

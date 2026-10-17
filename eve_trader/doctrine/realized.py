"""Doctrine realized-profit report and actions.

Reads the shared ledger. It does not run costing. One finished doctrine
contract is one result (U7). The page shows API numbers only: cost by
origin, per fitting, per contract with item lines and the production
chain, and the informational production saving (U9).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import storage
from ..actions import ActionError
from ..production.realized.report import _children, _walk
from ..realized import access, db, names as display_names
from ..realized.costing import ESTIMATED_COVERAGE_THRESHOLD, CostComponents

CHANNEL = "doctrine"
_BUY_SOURCES = ("buy_home", "buy_hub")


def _scope() -> dict:
    """Doctrine contracts only, and only for owners who share contracts with Doctrine."""
    return {
        "channel": CHANNEL,
        "wallet_owners": [],
        "contract_owners": list(access.shared_owners_for("contracts", "doctrine")),
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


def _as_date(value):
    if isinstance(value, datetime):
        return _as_utc(value).date()
    return value


def _num(value) -> float:
    return 0.0 if value is None else float(value)


def _warnings(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _draw_cost(row) -> float:
    return sum(_num(row[i]) for i in range(8, 14))


def _components_from(row, offset: int) -> CostComponents:
    return CostComponents(
        materials_bought=_num(row[offset]),
        materials_estimated=_num(row[offset + 1]),
        freight=_num(row[offset + 2]),
        job=_num(row[offset + 3]),
        invention=_num(row[offset + 4]),
        fees=_num(row[offset + 5]),
    )


def _coverage(components: CostComponents, price_missing: bool) -> tuple[Optional[float], bool]:
    cov = components.coverage()
    if cov is None:
        cov = 0.0 if price_missing else 1.0
    estimated = bool(price_missing) or cov < ESTIMATED_COVERAGE_THRESHOLD
    return cov, estimated


def _margin(profit: float, revenue: float) -> Optional[float]:
    if not revenue:
        return None
    return profit / revenue


def _flags(*, estimated: bool, price_missing: bool, coverage: Optional[float] = None) -> dict:
    return {
        "estimated": bool(estimated),
        "price_missing": bool(price_missing),
        "assumed_me": False,
        "assumed_decryptor": False,
        "coverage": coverage,
        "tax_missing": False,
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


def _status(run, t0, results_ever: int, *, sharing_changed: bool) -> dict:
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
        hint = f"no doctrine contracts in the ledger yet; results start at {_iso(t0)}"
        empty = True
    else:
        hint = None
        empty = False
    return {
        "empty": empty,
        "needs_rebuild": run is None or sharing_changed,
        "sharing_changed": bool(sharing_changed),
        "stale": False,
        "hint": hint,
        "cutover_t0": _iso(t0),
        "archive_since": None,
        "last_recompute": None if run is None else _iso(run[1]),
        "last_wallet_sync": None,
        "last_jobs_sync": None,
        "run_id": None if run is None else int(run[0]),
    }


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


def _empty_summary(period: dict, status: dict) -> dict:
    return {
        "period": period,
        "status": status,
        "contracts": 0,
        "revenue": 0.0,
        "cost_of_goods_sold": 0.0,
        "realized_profit": 0.0,
        "margin": None,
        "coverage": None,
        "estimated": False,
        "price_missing": False,
        "production_saving": None,
        "cost_by_origin": [],
        "by_day": [],
        "flags": _flags(estimated=False, price_missing=False, coverage=None),
    }


def _history_by_contract() -> dict[int, tuple]:
    try:
        rows = storage.load_doctrine_contract_history()
    except Exception:  # noqa: BLE001 - a missing history table is "no names"
        return {}
    return {int(row[0]): row for row in rows}


def _fitting_names(fitting_ids: set[str], history: dict[int, tuple]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in history.values():
        if row[2] and row[3]:
            names[str(row[2])] = str(row[3])
    for fitting_id in fitting_ids:
        if fitting_id in names:
            continue
        try:
            row = storage.get_fitting(fitting_id)
        except Exception:  # noqa: BLE001
            row = None
        if row is not None and row[2]:
            names[fitting_id] = str(row[2])
    return names


def _contract_id(sale_ref: str) -> Optional[int]:
    parts = str(sale_ref).split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    return int(parts[1])


def _origin_rows(draws: list[tuple], names: dict[int, str], broker_fee: float) -> list[dict]:
    """Cost by origin. Market purchases are one row per location (U4's hubs)."""
    buckets: dict[tuple, dict] = {}
    for row in draws:
        origin = str(row[6])
        source = None if row[24] is None else str(row[24])
        location_id = None if row[21] is None else int(row[21])
        if origin == "produced":
            key = ("produced", None, None)
            kind = "produced"
        elif origin == "bought" and source == "contract":
            key = ("contract", None, None)
            kind = "contract"
        elif origin == "bought":
            key = ("market", location_id, None)
            kind = "market"
        elif origin in ("opening", "unknown"):
            key = (origin, None, None)
            kind = origin
        else:
            key = (origin, location_id, source)
            kind = origin
        slot = buckets.get(key)
        if slot is None:
            slot = {
                "kind": kind,
                "location_id": location_id if kind == "market" else None,
                "location_name": None,
                "cost": 0.0,
                "quantity": 0.0,
            }
            buckets[key] = slot
        slot["cost"] += _draw_cost(row)
        slot["quantity"] += _num(row[5])
    if broker_fee:
        buckets[("broker_fees", None, None)] = {
            "kind": "broker_fees",
            "location_id": None,
            "location_name": None,
            "cost": broker_fee,
            "quantity": 0.0,
        }
    location_ids = [slot["location_id"] for slot in buckets.values() if slot["location_id"]]
    locations = display_names.location_names(location_ids) if location_ids else {}
    rows = []
    for slot in buckets.values():
        if slot["location_id"] is not None:
            slot["location_name"] = locations.get(int(slot["location_id"]))
        rows.append(slot)
    rows.sort(key=lambda slot: (slot["kind"], slot["location_id"] or 0))
    del names
    return rows


def _buy_price(prices: dict, type_id: int, day) -> Optional[float]:
    best = None
    best_day = None
    for (tid, price_date), (value, source) in prices.items():
        if int(tid) != int(type_id) or source not in _BUY_SOURCES or value is None:
            continue
        priced = _as_date(price_date)
        if priced <= day and (best_day is None or priced > best_day):
            best_day = priced
            best = float(value)
    return best


def _production_saving(draws: list[tuple], sale_date: datetime) -> Optional[float]:
    """Planner buy price minus production cost, for the produced lines (U9).

    None when any produced line has no frozen buy price. A build-only
    valuation is not a buy price, so it does not count.
    """
    produced = [row for row in draws if str(row[6]) == "produced"]
    if not produced:
        return None
    type_ids = sorted({int(row[3]) for row in produced})
    prices = db.list_cached_valuation_prices(type_ids)
    day = _as_utc(sale_date).date() if isinstance(sale_date, datetime) else sale_date
    buy = 0.0
    cost = 0.0
    for row in produced:
        price = _buy_price(prices, int(row[3]), day)
        if price is None:
            return None
        buy += price * _num(row[5])
        cost += _draw_cost(row)
    return buy - cost


def _attach_names(nodes: list[dict], names: dict[int, str]) -> None:
    missing = {int(node["type_id"]) for node in _walk(nodes) if node.get("type_id") is not None} - set(names)
    extra = _names(missing) if missing else {}
    for node in _walk(nodes):
        tid = node.get("type_id")
        if tid is None or node.get("type_name"):
            continue
        node["type_name"] = names.get(int(tid)) or extra.get(int(tid))


def _lines(draws: list[tuple], names: dict[int, str]) -> list[dict]:
    grouped: dict[int, dict] = {}
    for row in draws:
        type_id = int(row[3])
        slot = grouped.get(type_id)
        if slot is None:
            slot = {
                "type_id": type_id,
                "type_name": names.get(type_id),
                "quantity": 0.0,
                "cost": 0.0,
                "origins": {},
                "draws": [],
            }
            grouped[type_id] = slot
        origin = str(row[6])
        cost = _draw_cost(row)
        slot["quantity"] += _num(row[5])
        slot["cost"] += cost
        slot["origins"][origin] = slot["origins"].get(origin, 0.0) + cost
        slot["draws"].append(row)
    lines = []
    for slot in grouped.values():
        qty = slot["quantity"]
        origin = max(slot["origins"], key=lambda key: (slot["origins"][key], key == "produced"))
        chain = []
        if "produced" in slot["origins"]:
            produced = [row for row in slot["draws"] if str(row[6]) == "produced"]
            chain = _children(produced, 0, set())
            _attach_names(chain, names)
        lines.append({
            "type_id": slot["type_id"],
            "type_name": slot["type_name"],
            "quantity": qty,
            "origin": origin,
            "unit_cost": (slot["cost"] / qty) if qty else None,
            "cost": slot["cost"],
            "origins": slot["origins"],
            "chain": chain,
        })
    lines.sort(key=lambda line: line["type_id"])
    return lines


def _load(start: datetime, end: datetime) -> tuple[list[tuple], dict[str, list], dict]:
    scope = _scope()
    results = db.list_scoped_results(start, end, **scope)
    draws = db.list_draws("sale", [str(row[0]) for row in results])
    grouped: dict[str, list] = defaultdict(list)
    for draw in draws:
        grouped[str(draw[0])].append(draw)
    return results, grouped, _history_by_contract()


def summary(*, start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    with storage.batch_session():
        run, t0, changed = _run_state()
        scope = _scope()
        results_ever = db.count_realized_results(**scope) if run is not None else 0
        status = _status(run, t0, results_ever, sharing_changed=changed)
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        period = _period_dict(None if window is None else window[0], end, since_t0, days)
        if window is None:
            return _empty_summary(period, status)
        results, grouped, _history = _load(*window)
        totals = db.realized_totals(*window, **scope)
        by_day = [
            {"date": _iso(row[0]), "revenue": _num(row[1]), "cost": _num(row[2]), "profit": _num(row[3])}
            for row in db.realized_by_day(*window, **scope)
        ]
    components = CostComponents(
        materials_bought=_num(totals[4]), materials_estimated=_num(totals[5]),
        freight=_num(totals[6]), job=_num(totals[7]), invention=_num(totals[8]),
        fees=_num(totals[9]),
    )
    price_missing = bool(totals[12])
    count = int(totals[13])
    cov, estimated = _coverage(components, price_missing) if count else (None, False)
    revenue = _num(totals[0])
    cost = _num(totals[1])
    profit = _num(totals[2])
    draws = [draw for rows in grouped.values() for draw in rows]
    broker = sum(_num(row[10]) for row in results)
    saving_parts = []
    for row in results:
        saving = _production_saving(grouped.get(str(row[0]), []), row[7])
        if saving is not None:
            saving_parts.append(saving)
    return {
        "period": period,
        "status": status,
        "contracts": count,
        "revenue": revenue,
        "cost_of_goods_sold": cost,
        "realized_profit": profit,
        "margin": _margin(profit, revenue),
        "coverage": cov,
        "estimated": estimated,
        "price_missing": price_missing,
        "production_saving": sum(saving_parts) if saving_parts else None,
        "cost_by_origin": _origin_rows(draws, {}, broker),
        "by_day": by_day,
        "flags": _flags(estimated=estimated, price_missing=price_missing, coverage=cov),
    }


def fittings(*, start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    with storage.batch_session():
        run, t0, changed = _run_state()
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        period = _period_dict(None if window is None else window[0], end, since_t0, days)
        if window is None:
            return {"period": period, "needs_rebuild": True, "rows": []}
        results, grouped, history = _load(*window)
    names = _fitting_names({str(row[24]) for row in results if row[24]}, history)
    buckets: dict[Optional[str], dict] = {}
    for row in results:
        fitting_id = None if row[24] is None else str(row[24])
        contract_id = _contract_id(str(row[0]))
        hist = history.get(contract_id) if contract_id is not None else None
        slot = buckets.get(fitting_id)
        if slot is None:
            if hist is not None and hist[3]:
                fitting_name = str(hist[3])
            else:
                fitting_name = names.get(fitting_id) if fitting_id else None
            slot = {
                "fitting_id": fitting_id,
                "fitting_name": fitting_name,
                "contracts": 0,
                "revenue": 0.0,
                "cost": 0.0,
                "profit": 0.0,
                "produced_cost": 0.0,
                "bought_cost": 0.0,
            }
            buckets[fitting_id] = slot
        slot["contracts"] += 1
        slot["revenue"] += _num(row[3])
        slot["cost"] += _num(row[4])
        slot["profit"] += _num(row[5])
        for draw in grouped.get(str(row[0]), []):
            amount = _draw_cost(draw)
            if str(draw[6]) == "produced":
                slot["produced_cost"] += amount
            elif str(draw[6]) == "bought":
                slot["bought_cost"] += amount
    rows = []
    for slot in buckets.values():
        n = slot["contracts"]
        cost = slot["cost"]
        rows.append({
            **slot,
            "avg_price": (slot["revenue"] / n) if n else None,
            "avg_cost": (cost / n) if n else None,
            "avg_profit": (slot["profit"] / n) if n else None,
            "produced_share": (slot["produced_cost"] / cost) if cost else None,
            "bought_share": (slot["bought_cost"] / cost) if cost else None,
        })
    rows.sort(key=lambda slot: (-slot["profit"], slot["fitting_name"] or ""))
    return {"period": period, "needs_rebuild": False, "rows": rows}


def _contract_row(row, draws: list[tuple], history: dict, fitting_names: dict, owner_names: dict) -> dict:
    contract_id = _contract_id(str(row[0]))
    hist = history.get(contract_id) if contract_id is not None else None
    fitting_id = None if row[24] is None else str(row[24])
    if hist is not None and hist[3] and (fitting_id is None or hist[2] is None or str(hist[2]) == fitting_id):
        fitting_name = str(hist[3])
    else:
        fitting_name = fitting_names.get(fitting_id) if fitting_id else None
    buyer_id = None if hist is None or hist[7] is None else int(hist[7])
    buyer_name = None if hist is None or not hist[8] else str(hist[8])
    issuer_id = None if row[25] is None else int(row[25])
    if buyer_name is None and buyer_id is not None:
        buyer_name = owner_names.get(buyer_id)
    components = _components_from(row, 15)
    price_missing = bool(row[12])
    cov, estimated = _coverage(components, price_missing)
    return {
        "sale_ref": str(row[0]),
        "contract_id": contract_id,
        "sale_date": _iso(row[7]),
        "fitting_id": fitting_id,
        "fitting_name": fitting_name,
        "buyer_id": buyer_id,
        "buyer_name": buyer_name,
        "issuer_id": issuer_id,
        "issuer_name": None if issuer_id is None else owner_names.get(issuer_id),
        "price": _num(row[8]),
        "revenue": _num(row[3]),
        "cost": _num(row[4]),
        "profit": _num(row[5]),
        "margin": _margin(_num(row[5]), _num(row[3])),
        "coverage": cov,
        "warnings": _warnings(row[14]),
        "production_saving": _production_saving(draws, row[7]),
        "before_archive": False,
        "flags": _flags(estimated=estimated, price_missing=price_missing, coverage=cov),
    }


def contracts(*, start: Optional[datetime], end: datetime, since_t0: bool, days: Optional[int]) -> dict:
    with storage.batch_session():
        run, t0, changed = _run_state()
        window = None if run is None or changed else _window(start, end, since_t0, t0)
        period = _period_dict(None if window is None else window[0], end, since_t0, days)
        if window is None:
            return {"period": period, "needs_rebuild": True, "rows": [], "before_archive": []}
        results, grouped, history = _load(*window)
        known = _known_contract_ids()
    fitting_names = _fitting_names({str(row[24]) for row in results if row[24]}, history)
    owner_ids = set()
    for row in results:
        if row[25] is not None:
            owner_ids.add(int(row[25]))
    for hist in history.values():
        if hist[7] is not None:
            owner_ids.add(int(hist[7]))
    owner_names = display_names.owner_names(owner_ids)
    rows = [
        _contract_row(row, grouped.get(str(row[0]), []), history, fitting_names, owner_names)
        for row in results
    ]
    before = _before_archive(history, known, window[0], window[1], owner_names)
    return {"period": period, "needs_rebuild": False, "rows": rows, "before_archive": before}


def _known_contract_ids() -> set[int]:
    with storage.connect() as conn:
        found = conn.execute(
            "SELECT sale_ref FROM prod_realized_results "
            "WHERE channel = 'doctrine' AND sale_ref LIKE 'contract:%'",
        ).fetchall()
    ids = set()
    for (ref,) in found:
        contract_id = _contract_id(str(ref))
        if contract_id is not None:
            ids.add(contract_id)
    return ids


def _before_archive(history: dict, known: set[int], start: datetime, end: datetime,
                    owner_names: dict) -> list[dict]:
    """Finished doctrine history the ledger never costed (section 7)."""
    rows = []
    for contract_id, hist in history.items():
        if contract_id in known:
            continue
        completed = hist[10]
        if isinstance(completed, datetime):
            completed = _as_utc(completed)
        if completed is not None and (completed < start or completed >= end):
            continue
        buyer_id = None if hist[7] is None else int(hist[7])
        rows.append({
            "sale_ref": None,
            "contract_id": contract_id,
            "sale_date": _iso(completed),
            "fitting_id": None if hist[2] is None else str(hist[2]),
            "fitting_name": None if hist[3] is None else str(hist[3]),
            "buyer_id": buyer_id,
            "buyer_name": (str(hist[8]) if hist[8] else None) or (
                None if buyer_id is None else owner_names.get(buyer_id)
            ),
            "issuer_id": None,
            "issuer_name": None,
            "price": None if hist[6] is None else float(hist[6]),
            "revenue": None,
            "cost": None,
            "profit": None,
            "margin": None,
            "coverage": None,
            "warnings": ["before_archive"],
            "production_saving": None,
            "before_archive": True,
            "flags": _flags(estimated=False, price_missing=False, coverage=None),
        })
    rows.sort(key=lambda row: row["sale_date"] or "")
    return rows


def contract(sale_ref: str) -> Optional[dict]:
    with storage.batch_session():
        run, _t0, changed = _run_state()
        if run is None or changed:
            return None
        row = db.list_scoped_results(
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(9999, 1, 1, tzinfo=timezone.utc),
            **_scope(),
        )
        match = next((item for item in row if str(item[0]) == sale_ref), None)
        if match is None:
            return None
        draws = db.list_draws("sale", [sale_ref])
        history = _history_by_contract()
    type_ids = {int(draw[3]) for draw in draws}
    if match[1]:
        type_ids.add(int(match[1]))
    names = _names(type_ids)
    fitting_names = _fitting_names({str(match[24])} if match[24] else set(), history)
    owner_ids = set()
    if match[25] is not None:
        owner_ids.add(int(match[25]))
    contract_id = _contract_id(sale_ref)
    hist = history.get(contract_id) if contract_id is not None else None
    if hist is not None and hist[7] is not None:
        owner_ids.add(int(hist[7]))
    detail = _contract_row(match, draws, history, fitting_names, display_names.owner_names(owner_ids))
    detail["lines"] = _lines(draws, names)
    detail["cost_by_origin"] = _origin_rows(draws, names, _num(match[10]))
    return detail


def warnings() -> dict:
    with storage.batch_session():
        run, _t0, changed = _run_state()
        if run is None or changed:
            return {"needs_rebuild": True, "rows": []}
        rows = db.realized_warnings_by_type(
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(9999, 1, 1, tzinfo=timezone.utc),
            **_scope(),
        )
    return {
        "needs_rebuild": False,
        "rows": [{"type_id": int(type_id), "warning": str(warning)} for type_id, warning in rows],
    }


def portfolio_figure() -> dict:
    """Last 30 days, for the Portfolio card. Empty until a recompute."""
    now = datetime.now(timezone.utc)
    data = summary(start=now - timedelta(days=30), end=now, since_t0=False, days=30)
    empty = data["status"]["empty"] or data["status"]["needs_rebuild"]
    if empty:
        return {"profit": None, "coverage": None, "estimated": False, "empty": True}
    return {
        "profit": data["realized_profit"],
        "coverage": data["coverage"],
        "estimated": data["estimated"],
        "empty": False,
    }


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


def do_get_doctrine_realized_summary(days: int | None = None, start: str | None = None,
                                     end: str | None = None, since_t0: bool = False) -> dict:
    return summary(**_period(days, start, end, since_t0))


def do_get_doctrine_realized_fittings(days: int | None = None, start: str | None = None,
                                      end: str | None = None, since_t0: bool = False) -> dict:
    return fittings(**_period(days, start, end, since_t0))


def do_get_doctrine_realized_contracts(days: int | None = None, start: str | None = None,
                                       end: str | None = None, since_t0: bool = False) -> dict:
    return contracts(**_period(days, start, end, since_t0))


def do_get_doctrine_realized_contract(sale_ref: str) -> dict:
    ref = (sale_ref or "").strip()
    if not ref or len(ref) > 200:
        raise ActionError("sale_ref is required.")
    found = contract(ref)
    if found is None:
        raise ActionError(f"No realized contract {ref}.")
    return found


def do_get_doctrine_realized_warnings() -> dict:
    return warnings()


def do_start_doctrine_realized_rebuild() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.start_rebuild_realized()


def do_doctrine_realized_rebuild_status() -> dict:
    from .. import pipeline_runner
    return pipeline_runner.job_status(
        pipeline_runner.TOOL_PRODUCTION, pipeline_runner.JOB_REBUILD_REALIZED,
    )

"""Dynamic stock target quantities (docs/DYNAMIC_STOCK_TARGETS_PLAN.md).

The item list of the Stock Targets page stays exactly as the user maintains
it; only the *home-market* quantity of rows switched to `dynamic` becomes a
computed number: own sales velocity (or the market estimate) x coverage days x
a profitability factor, smoothed, dead-banded and clamped to the row's
min/max. The result is frozen once per UTC day (`stock_target_dynamic_values`)
so both planners and the cheap pages read the same number.

Layout of this module:
  1. pure resolver (`resolve_row`) - no I/O, table-driven tests
  2. signals (own sales from the realized ledger, market estimate, margins)
  3. freeze lifecycle + the read side (`effective_stock_targets`)
  4. preview / recalculate (the two explicit entry points that may write or
     fetch outside a plan run)

Everything here is behind the global switch `dynamic_targets_enabled` (D9):
with it off no function in this module reads or writes anything beyond what
the pre-feature code did (plan P-02).
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

from .. import storage
from .config import PRODUCTION_CONFIG, ProductionConfig

log = logging.getLogger(__name__)

# goonmetrics_client.HISTORY_WINDOW_DAYS: the market history source returns at
# most this many days (review finding V-02), so the market side never uses a
# longer window than this whatever dynamic_target_window_days says.
MARKET_HISTORY_MAX_DAYS = 28

REASON_OK = "ok"
REASON_HELD = "held_no_data"
REASON_BELOW_MIN_MARGIN = "below_min_margin"
REASON_CLAMPED_MIN = "clamped_min"
REASON_CLAMPED_MAX = "clamped_max"
REASON_WITHIN_DEADBAND = "within_deadband"
REASON_MIN_ONE = "min_one"
REASONS = (REASON_OK, REASON_HELD, REASON_BELOW_MIN_MARGIN, REASON_CLAMPED_MIN, REASON_CLAMPED_MAX,
           REASON_WITHIN_DEADBAND, REASON_MIN_ONE)

MODE_FIXED = "fixed"
MODE_DYNAMIC = "dynamic"

_UNSET = object()


# ----------------------------------------------------------------- time helpers
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    """UTC calendar day (= EVE time). Passed to SQL as a parameter; Postgres
    CURRENT_DATE follows the session time zone and can be another day (P-18)."""
    return _utcnow().date()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# --------------------------------------------------------------------- bounds
def default_max(configured_home: float) -> float:
    """D13: max defaults to max(3 x the current fixed home value, 10)."""
    return max(3.0 * float(configured_home or 0.0), 10.0)


def default_bounds(configured_home: float, dynamic_min: Optional[float],
                   dynamic_max: Optional[float]) -> tuple[float, float]:
    row_min = float(dynamic_min) if dynamic_min is not None else 0.0
    row_max = float(dynamic_max) if dynamic_max is not None else default_max(configured_home)
    return row_min, max(row_min, row_max)


# ============================================================ 1. pure resolver
@dataclass(frozen=True)
class RowInput:
    configured_home: float
    row_min: float
    row_max: float
    velocity_own: Optional[float]       # None = unavailable
    velocity_market: Optional[float]    # None = unavailable
    has_own_sale: bool                  # at least one own market sale in the window (D12)
    margin: Optional[float]             # None = unavailable
    prev_effective: Optional[float] = None   # newest frozen value strictly before today
    prev_smoothed: Optional[float] = None
    prev_reason: Optional[str] = None


@dataclass(frozen=True)
class RowResult:
    effective_qty: float
    smoothed_qty: float
    raw_qty: Optional[float]
    velocity_used: Optional[float]
    velocity_source: Optional[str]      # 'own' | 'market' | None
    factor: Optional[float]
    reason: str


def combine_velocity(own: Optional[float], market: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """D2 / section 5.3: max(own, market); one alone when the other is
    unavailable; none when both are."""
    if own is None and market is None:
        return None, None
    if own is None:
        return market, "market"
    if market is None:
        return own, "own"
    return (own, "own") if own >= market else (market, "market")


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def profit_factor(margin: float, cfg: ProductionConfig) -> float:
    """Section 6: clamp((margin - min_margin) / (reference - min_margin),
    factor_min, factor_max); 1.0 when the reference no longer exceeds
    min_margin (E-16)."""
    reference = cfg.dynamic_target_reference_margin
    if reference <= cfg.min_margin:
        return 1.0
    raw = (margin - cfg.min_margin) / (reference - cfg.min_margin)
    return min(cfg.dynamic_target_factor_max, max(cfg.dynamic_target_factor_min, raw))


def resolve_row(inp: RowInput, cfg: ProductionConfig, *, converged: bool = False) -> RowResult:
    """Section 6 of the plan, one row. `converged=True` computes the value the
    row settles at (smoothing 1, no dead band) - used by the preview only."""
    prev_effective = inp.prev_effective if inp.prev_effective is not None else float(inp.configured_home)
    prev_smoothed = inp.prev_smoothed if inp.prev_smoothed is not None else float(inp.configured_home)
    velocity, source = combine_velocity(inp.velocity_own, inp.velocity_market)

    # 1. No velocity or no margin: hold (E-04). A data gap never moves a target.
    if velocity is None or inp.margin is None:
        return RowResult(prev_effective, prev_smoothed, None, velocity, source, None, REASON_HELD)

    # 2. Below min_margin: effective 0 (D5), smoothed level kept (E-06).
    if inp.margin < cfg.min_margin:
        return RowResult(0.0, prev_smoothed, None, velocity, source, None, REASON_BELOW_MIN_MARGIN)

    # 3. Normal path.
    factor = profit_factor(inp.margin, cfg)
    raw = velocity * cfg.dynamic_target_coverage_days * factor
    alpha = 1.0 if converged else cfg.dynamic_target_smoothing
    smoothed = prev_smoothed + alpha * (raw - prev_smoothed)

    candidate = _round_half_up(smoothed)
    reason = REASON_OK
    if inp.has_own_sale and candidate < 1:
        candidate = 1  # D12: a row that sells never rounds below one unit
        reason = REASON_MIN_ONE
    if candidate < inp.row_min:
        candidate = inp.row_min
        reason = REASON_CLAMPED_MIN
    if candidate > inp.row_max:
        candidate = inp.row_max
        reason = REASON_CLAMPED_MAX
    effective = float(candidate)

    # Dead band on the rounded value (E-08). Skipped when coming back from 0
    # (that 0 is not a real previous level) and in the converged view.
    if not converged and inp.prev_reason != REASON_BELOW_MIN_MARGIN:
        if abs(candidate - prev_effective) < max(1.0, cfg.dynamic_target_deadband * prev_effective):
            effective = prev_effective
            reason = REASON_WITHIN_DEADBAND
    # A previous value can sit outside bounds the user changed since.
    effective = min(inp.row_max, max(inp.row_min, effective))
    return RowResult(effective, smoothed, raw, velocity, source, factor, reason)


# ================================================================ 2. signals
@dataclass(frozen=True)
class OwnRow:
    velocity: float
    units: float
    max_day_share: float
    realized_margin: Optional[float]


@dataclass(frozen=True)
class OwnSales:
    available: bool                     # ledger usable AND >= min_history covered days
    reason: Optional[str]               # why not available
    covered_days: float
    per_type: dict                      # type_id -> OwnRow (may be filled even if not available)


UNAVAILABLE_OWN = OwnSales(False, "not_loaded", 0.0, {})


def compute_own_sales(cfg: ProductionConfig, *, now: Optional[datetime] = None) -> OwnSales:
    """Section 5.1: units/day per type from the realized ledger, market sales
    at the home location only, in Production's channel/owner scope. The run
    state check (D11 fingerprint) is the report's own - no logic copied
    (P-12). Daily units are capped at `dynamic_target_day_cap_multiple` x the
    row's mean day (V-04) so one bulk day does not set a 14-day target."""
    from ..realized import db as realized_db
    from .realized import report

    if cfg.home_location_id is None:
        return OwnSales(False, "no_home_location", 0.0, {})
    run, t0, sharing_changed = report.run_state()
    if run is None:
        return OwnSales(False, "no_ledger_run", 0.0, {})
    if sharing_changed:
        return OwnSales(False, "sharing_changed", 0.0, {})
    if t0 is None or run[1] is None:
        return OwnSales(False, "no_cutover", 0.0, {})

    now = _as_utc(now) if now is not None else _utcnow()
    end = min(now, _as_utc(run[1]))  # the derived rows only reach the last rebuild (E-03)
    start = max(end - timedelta(days=cfg.dynamic_target_window_days), _as_utc(t0))
    covered = (end - start).total_seconds() / 86400.0
    if covered <= 0:
        return OwnSales(False, "no_history", 0.0, {})
    rows = realized_db.market_units_sold_daily(start, end, cfg.home_location_id, **report.production_scope())

    by_type: dict[int, list[tuple[float, Optional[float], Optional[float]]]] = {}
    for type_id, _day, units, profit, cost in rows:
        by_type.setdefault(int(type_id), []).append(
            (float(units or 0.0), None if profit is None else float(profit), None if cost is None else float(cost)))
    divisor = max(covered, 1.0)
    per_type: dict[int, OwnRow] = {}
    for type_id, days in by_type.items():
        total = sum(d[0] for d in days)
        if total <= 0:
            continue
        mean_day = total / divisor
        cap = cfg.dynamic_target_day_cap_multiple * mean_day if cfg.dynamic_target_day_cap_multiple > 0 else math.inf
        capped = sum(min(d[0], cap) for d in days)
        profit_sum = sum(d[1] for d in days if d[1] is not None)
        cost_sum = sum(d[2] for d in days if d[2] is not None)
        margin = (profit_sum / cost_sum) if cost_sum > 0 else None
        per_type[type_id] = OwnRow(capped / divisor, total, max(d[0] for d in days) / total, margin)

    if covered < cfg.dynamic_target_min_history_days:
        return OwnSales(False, "history_too_short", covered, per_type)
    return OwnSales(True, None, covered, per_type)


def compute_market_velocity(cfg: ProductionConfig, type_ids: Iterable[int], *, today: Optional[date] = None,
                            client=None, region_id: Optional[int] = None) -> dict[int, float]:
    """Section 5.2: units/day the region trades x dynamic_target_market_share,
    over the calendar days of min(window, 28) ending yesterday. Days without a
    point count as 0 (P-13: ESI omits days without trades; averaging only the
    returned points overstated slow items up to 30x). A type with no point at
    all is left out (unavailable). A network failure is logged and treated as
    unavailable - it never fails a plan."""
    ids = sorted({int(t) for t in type_ids})
    if not ids:
        return {}
    today = today or _today()
    days = max(1, min(cfg.dynamic_target_window_days, MARKET_HISTORY_MAX_DAYS))
    first = (today - timedelta(days=days)).isoformat()
    last = (today - timedelta(days=1)).isoformat()
    try:
        if client is None:
            from ..goonmetrics_client import GoonmetricsClient
            client = GoonmetricsClient()
        if region_id is None:
            from ..config import TRADING_CONFIG
            region_id = TRADING_CONFIG.reference_region_id
        points = client.price_history_chunked(region_id, ids)
    except Exception:  # noqa: BLE001 - a history outage must not fail the plan
        log.exception("Dynamic stock targets: market history unavailable - using own sales only.")
        return {}
    totals: dict[int, float] = {}
    for point in points:
        if first <= point.date <= last:
            totals[point.type_id] = totals.get(point.type_id, 0.0) + float(point.movement)
    return {t: total / days * cfg.dynamic_target_market_share for t, total in totals.items()}


@dataclass(frozen=True)
class MarginInfo:
    margin: Optional[float]
    cost: Optional[float]
    gate_margin: Optional[float]
    gate_blocked: bool


def compute_margins(cfg: ProductionConfig, ctx, rows: list, cost_memo: dict, t2_memo: dict,
                    alchemy_memo: dict) -> dict[int, MarginInfo]:
    """Section 5.4 / P-07 / P-08: the live margin at the home market with the
    cost the planner itself would use (same memos, same arguments), plus the
    margin the planner *gate* uses (V-10) so the page can show why a row is not
    built. D8: a manual Buy override uses the buy price as cost."""
    from . import engine, pricing

    out: dict[int, MarginInfo] = {}
    for row in rows:
        type_id = row[0]
        jita_target = row[4]
        _activity, bp = engine.classify_activity(type_id)
        override = ctx.manual_overrides.get(type_id)
        cost = None
        gate_margin = None
        if bp is not None:
            engine._unit_cost(type_id, cfg, ctx.home, ctx.jita, cost_memo, ctx.selected_decryptors, t2_memo,
                              ctx.cost_indices, ctx.adjusted_prices, 0, alchemy_memo)
            cost = cost_memo.get(type_id)
            if override is None:
                gate_margin = engine._build_margin(type_id, cost, jita_target, ctx.home, ctx.jita, cfg)
        if override == "Buy":
            cost = pricing.buy_price(type_id, ctx.home, ctx.jita, engine._haul_volume(type_id, cfg), cfg)
        margin = engine.margin_home(type_id, cost, ctx.home, cfg) if cost else None
        blocked = bool(gate_margin is not None and gate_margin < cfg.min_margin)
        out[type_id] = MarginInfo(margin, cost, gate_margin, blocked)
    return out


# =================================================== row selection and combine
@dataclass(frozen=True)
class RowSpec:
    type_id: int
    type_name: str
    backup: float
    configured_home: float
    jita: float
    mode: str
    dynamic_min: Optional[float]
    dynamic_max: Optional[float]

    @property
    def stock_row(self) -> tuple:
        return (self.type_id, self.type_name, self.backup, self.configured_home, self.jita)


def build_specs(stock_rows: Iterable[tuple], modes: dict) -> list[RowSpec]:
    specs = []
    for type_id, name, backup, home, jita in stock_rows:
        mode, dmin, dmax = modes.get(int(type_id), (MODE_FIXED, None, None))
        specs.append(RowSpec(int(type_id), name, float(backup or 0.0), float(home or 0.0), float(jita or 0.0),
                             mode, dmin, dmax))
    return specs


@dataclass(frozen=True)
class Signals:
    own: OwnSales
    market: dict
    margins: dict
    region_id: Optional[int]


def resolve_specs(cfg: ProductionConfig, specs: list[RowSpec], signals: Signals, previous: dict,
                  *, converged: bool = False) -> list[dict]:
    """Pure: RowSpec + signals + previous frozen rows -> storage-shaped dicts
    (plus `type_name`, `cost` for the preview). No I/O."""
    out = []
    for spec in specs:
        own_row = signals.own.per_type.get(spec.type_id)
        info = signals.margins.get(spec.type_id)
        live = info.margin if info is not None else None
        if live is not None:
            margin, margin_source = live, "live"
        elif own_row is not None and own_row.realized_margin is not None:
            margin, margin_source = own_row.realized_margin, "realized"  # E-11
        else:
            margin, margin_source = None, None
        prev = previous.get(spec.type_id)
        row_min, row_max = default_bounds(spec.configured_home, spec.dynamic_min, spec.dynamic_max)
        result = resolve_row(RowInput(
            configured_home=spec.configured_home, row_min=row_min, row_max=row_max,
            velocity_own=own_row.velocity if (own_row is not None and signals.own.available) else
            (0.0 if signals.own.available else None),
            velocity_market=signals.market.get(spec.type_id),
            has_own_sale=own_row is not None,
            margin=margin,
            prev_effective=None if prev is None else float(prev["effective_qty"]),
            prev_smoothed=None if prev is None else float(prev["smoothed_qty"]),
            prev_reason=None if prev is None else prev["reason"],
        ), cfg, converged=converged)
        out.append({
            "type_id": spec.type_id, "type_name": spec.type_name,
            "effective_qty": result.effective_qty, "smoothed_qty": result.smoothed_qty, "raw_qty": result.raw_qty,
            "velocity_own": own_row.velocity if (own_row is not None and signals.own.available) else
            (0.0 if signals.own.available else None),
            "velocity_market": signals.market.get(spec.type_id),
            "velocity_used": result.velocity_used, "velocity_source": result.velocity_source,
            "margin": margin, "margin_source": margin_source, "factor": result.factor, "reason": result.reason,
            "gate_margin": None if info is None else info.gate_margin,
            "gate_blocked": None if info is None else info.gate_blocked,
            "max_day_share": None if own_row is None else own_row.max_day_share,
            "region_id": signals.region_id,
            "cost": None if info is None else info.cost,
            "configured_home": spec.configured_home,
        })
    return out


def _gather_signals(cfg: ProductionConfig, ctx, rows: list, cost_memo: dict, t2_memo: dict, alchemy_memo: dict,
                    *, today: date, client=None) -> Signals:
    """The I/O half: ledger read, market history fetch, margins. Called with
    no write pending, so a slow history fetch never holds a half-written
    transaction (P-14/P-23)."""
    from ..config import TRADING_CONFIG

    own = compute_own_sales(cfg)
    region_id = TRADING_CONFIG.reference_region_id
    market = compute_market_velocity(cfg, [r[0] for r in rows], today=today, client=client, region_id=region_id)
    margins = compute_margins(cfg, ctx, rows, cost_memo, t2_memo, alchemy_memo)
    return Signals(own, market, margins, region_id)


def _previous_rows(cfg: ProductionConfig, day: date) -> dict:
    """Smoothing base: newest frozen row strictly before `day` (E-05); older
    than the window it restarts from the configured value (E-25)."""
    previous = storage.latest_dynamic_value_before(day)
    cutoff = day - timedelta(days=cfg.dynamic_target_window_days)
    return {t: row for t, row in previous.items() if row["day"] >= cutoff}


# ====================================================== 3. freeze and read side
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_invalidated_at: Optional[datetime] = None  # process-wide, set by an all-tenant invalidation (P-21)


def _tenant_lock() -> threading.Lock:
    """One lock per tenant, so one tenant's first plan of the day never blocks
    another's (P-19)."""
    key = str(storage.get_current_tenant())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def mark_all_invalidated() -> None:
    """Allowlisted trigger for an SDE apply (no ambient tenant, so no RLS rows
    can be deleted): a frozen row computed before this instant counts as
    missing (P-21). Lost on restart - the next day recomputes anyway."""
    global _invalidated_at
    _invalidated_at = _utcnow()


def invalidate_today(type_ids: Optional[Iterable[int]] = None) -> int:
    """Drops today's frozen rows (all, or only `type_ids`) so the next plan run
    computes them again. Takes the tenant lock first so an in-flight freeze
    built from the old settings cannot overwrite the invalidation (P-22).
    Only called from the closed allowlist of review finding V-01 (settings
    save, row mode/min/max change, stock target add/remove, recalculate,
    switch toggle, bulk action, SDE apply) - never from
    `invalidate_discover_cache`."""
    with _tenant_lock():
        return storage.delete_dynamic_values_for_day(_today(), None if type_ids is None else list(type_ids))


def _row_is_current(row: dict) -> bool:
    if _invalidated_at is None:
        return True
    computed = row.get("computed_at")
    return computed is None or _as_utc(computed) >= _invalidated_at


def dynamic_specs(stock_rows: Iterable[tuple], modes: dict) -> list[RowSpec]:
    return [s for s in build_specs(stock_rows, modes) if s.mode == MODE_DYNAMIC]


def ensure_dynamic_targets_frozen(cfg: ProductionConfig, ctx, cost_memo: dict, t2_memo: dict, alchemy_memo: dict,
                                  *, modes: Optional[dict] = None, force: bool = False, client=None) -> int:
    """Section 7: computes and writes today's frozen value for every dynamic
    row that has none yet (or all of them with `force`). Returns the number of
    rows written. The planners call this before sizing anything; with the
    switch off, or no dynamic row, it does nothing at all (E-19)."""
    if not getattr(cfg, 'dynamic_targets_enabled', False):
        return 0
    modes = modes if modes is not None else storage.load_stock_target_modes()
    specs = dynamic_specs(ctx.stock_targets, modes)
    if not specs:
        return 0
    day = _today()
    with _tenant_lock():
        existing = storage.load_dynamic_values_for_day(day)
        todo = [s for s in specs if force or s.type_id not in existing or not _row_is_current(existing[s.type_id])]
        if not todo:
            return 0
        previous = _previous_rows(cfg, day)
        if cfg.home_location_id is None:  # E-18: nothing to measure against, hold
            signals = Signals(UNAVAILABLE_OWN, {}, {}, None)
        else:
            signals = _gather_signals(cfg, ctx, [s.stock_row for s in todo], cost_memo, t2_memo, alchemy_memo,
                                      today=day, client=client)
        results = resolve_specs(cfg, todo, signals, previous)
        written = storage.freeze_dynamic_values(day, [_storage_row(r) for r in results])
    return written


def _storage_row(result: dict) -> dict:
    return {k: v for k, v in result.items() if k in storage.DYNAMIC_VALUE_COLUMNS}


def effective_stock_targets(cfg: ProductionConfig, rows: Optional[Iterable[tuple]] = None, *,
                            modes: Optional[dict] = None, day: Optional[date] = None) -> list[tuple]:
    """The 5-tuples every consumer reads, with the home value of each dynamic
    row replaced by its newest frozen value on or before today (the configured
    value while nothing is frozen yet). Read-only, no pricing. Switch off
    (or no dynamic row): `rows` / `storage.load_stock_targets()` unchanged and
    nothing else is read (P-01/P-02)."""
    base = list(rows) if rows is not None else storage.load_stock_targets()
    if not getattr(cfg, 'dynamic_targets_enabled', False):
        return base
    modes = modes if modes is not None else storage.load_stock_target_modes()
    dynamic = {t for t, (mode, _lo, _hi) in modes.items() if mode == MODE_DYNAMIC}
    if not dynamic:
        return base
    frozen = storage.load_dynamic_values_on_or_before(day or _today())
    out = []
    for row in base:
        value = frozen.get(row[0]) if row[0] in dynamic else None
        out.append(row if value is None else (row[0], row[1], row[2], float(value["effective_qty"]), row[4]))
    return out


def dynamic_type_ids(cfg: ProductionConfig = PRODUCTION_CONFIG) -> set[int]:
    """Type ids of dynamic rows; empty with the switch off. The market-intent
    rules (market_status #33, unlisted-stock check) keep these visible even at
    an effective 0 (E-09, E-10)."""
    if not getattr(cfg, 'dynamic_targets_enabled', False):
        return set()
    return {t for t, (mode, _lo, _hi) in storage.load_stock_target_modes().items() if mode == MODE_DYNAMIC}


def apply_dynamic_targets(cfg: ProductionConfig, ctx, cost_memo: dict, t2_memo: dict, alchemy_memo: dict) -> list[tuple]:
    """Planner entry (section 8): freeze today's values if needed and return
    the effective stock target rows. The caller assigns them to
    `ctx.stock_targets` *before* `_base_runs` so the overbuild buffer and the
    shortfall use the same numbers (P-06). A failure here must not fail the
    plan: it logs and falls back to the last frozen / configured values."""
    if not getattr(cfg, 'dynamic_targets_enabled', False):
        return ctx.stock_targets
    modes = storage.load_stock_target_modes()
    if not any(modes.get(int(r[0]), (MODE_FIXED,))[0] == MODE_DYNAMIC for r in ctx.stock_targets):
        return ctx.stock_targets
    try:
        ensure_dynamic_targets_frozen(cfg, ctx, cost_memo, t2_memo, alchemy_memo, modes=modes)
    except Exception:  # noqa: BLE001 - never fail the plan for a target refresh
        log.exception("Dynamic stock targets: freeze failed - using the last frozen values.")
    return effective_stock_targets(cfg, ctx.stock_targets, modes=modes)


def list_stock_targets_view(cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[dict]:
    """Bare reads for GET /stock-targets: configured values, mode, and for a
    dynamic row the effective value with the inputs behind it (no pricing)."""
    rows = storage.load_stock_targets()
    modes = storage.load_stock_target_modes()
    frozen = storage.load_dynamic_values_on_or_before(_today()) if getattr(cfg, 'dynamic_targets_enabled', False) else {}
    out = []
    for type_id, name, backup, home, jita in rows:
        mode, dmin, dmax = modes.get(int(type_id), (MODE_FIXED, None, None))
        value = frozen.get(int(type_id)) if (getattr(cfg, 'dynamic_targets_enabled', False) and mode == MODE_DYNAMIC) else None
        item = {
            "type_id": type_id, "type_name": name, "backup_stock": backup,
            "home_market_stock": home, "jita_market_stock": jita,
            "target_mode": mode, "dynamic_min": dmin, "dynamic_max": dmax,
            "effective_home_market_stock": home if value is None else float(value["effective_qty"]),
            "dynamic_day": None, "reason": None, "velocity_source": None, "velocity_used": None,
            "velocity_own": None, "velocity_market": None, "margin": None, "margin_source": None, "factor": None,
            "gate_margin": None, "gate_blocked": None, "max_day_share": None,
        }
        if value is not None:
            item.update({
                "dynamic_day": value["day"].isoformat(), "reason": value["reason"],
                "velocity_source": value["velocity_source"], "velocity_used": value["velocity_used"],
                "velocity_own": value["velocity_own"], "velocity_market": value["velocity_market"],
                "margin": value["margin"], "margin_source": value["margin_source"], "factor": value["factor"],
                "gate_margin": value["gate_margin"], "gate_blocked": value["gate_blocked"],
                "max_day_share": value["max_day_share"],
            })
        out.append(item)
    return out


# ==================================================== 4. preview and recalculate
def _make_context(cfg: ProductionConfig):
    from . import engine
    return engine._PlanContext(cfg)


def preview_dynamic_targets(cfg: ProductionConfig = PRODUCTION_CONFIG, *, ctx=None, client=None) -> dict:
    """Section 14.1: what the resolver would set for EVERY row with a home
    target (fixed rows included, switch ignored), with all inputs. Writes
    nothing. `day1` follows smoothing/dead band from the configured value (what
    the first run would freeze); `converged` is where the row settles."""
    rows = [r for r in storage.load_stock_targets() if (r[3] or 0) > 0]
    modes = storage.load_stock_target_modes()
    specs = build_specs(rows, modes)
    if not specs:
        return {"rows": [], "summary": _summary([], None), "own": _own_info(UNAVAILABLE_OWN)}
    ctx = ctx if ctx is not None else _make_context(cfg)
    cost_memo, t2_memo, alchemy_memo = {}, {}, {}
    day = _today()
    signals = _gather_signals(cfg, ctx, [s.stock_row for s in specs], cost_memo, t2_memo, alchemy_memo,
                              today=day, client=client)
    day1 = resolve_specs(cfg, specs, signals, {})
    converged = {r["type_id"]: r for r in resolve_specs(cfg, specs, signals, {}, converged=True)}
    out = []
    for r in day1:
        c = converged[r["type_id"]]
        cost = r.get("cost") or 0.0
        out.append({
            **r,
            "mode": next(s.mode for s in specs if s.type_id == r["type_id"]),
            "day1_qty": r["effective_qty"], "converged_qty": c["effective_qty"],
            "tied_current": cost * r["configured_home"], "tied_day1": cost * r["effective_qty"],
            "tied_converged": cost * c["effective_qty"],
        })
    return {"rows": out, "summary": _summary(out, signals), "own": _own_info(signals.own)}


def _own_info(own: OwnSales) -> dict:
    return {"available": own.available, "reason": own.reason, "covered_days": own.covered_days}


def _summary(rows: list[dict], signals: Optional[Signals]) -> dict:
    def count(pred: Callable[[dict], bool]) -> int:
        return sum(1 for r in rows if pred(r))

    gate_ok = [r for r in rows if not r.get("gate_blocked")]
    return {
        "rows": len(rows),
        "held": count(lambda r: r["reason"] == REASON_HELD),
        "below_min_margin": count(lambda r: r["reason"] == REASON_BELOW_MIN_MARGIN),
        "clamped_max": count(lambda r: r["reason"] == REASON_CLAMPED_MAX),
        "up": count(lambda r: r["converged_qty"] > r["configured_home"]),
        "down": count(lambda r: 0 < r["converged_qty"] < r["configured_home"]),
        "to_zero": count(lambda r: r["converged_qty"] == 0 and r["configured_home"] > 0),
        "unchanged": count(lambda r: r["converged_qty"] == r["configured_home"]),
        "tied_current": sum(r["tied_current"] for r in gate_ok),
        "tied_day1": sum(r["tied_day1"] for r in gate_ok),
        "tied_converged": sum(r["tied_converged"] for r in gate_ok),
        "velocity_source": {s: count(lambda r, s=s: r["velocity_source"] == s) for s in ("own", "market")},
    }


def recalculate_dynamic_targets(cfg: ProductionConfig = PRODUCTION_CONFIG, *, ctx=None, client=None) -> dict:
    """The "Recalculate" button: drops today's rows and runs the same freeze a
    plan run would. Raises ValueError when there is no home location (E-18) so
    the action can turn it into an ActionError."""
    if cfg.home_location_id is None:
        raise ValueError("No home location configured (Settings -> Market & Location).")
    rows = storage.load_stock_targets()
    modes = storage.load_stock_target_modes()
    if not dynamic_specs(rows, modes):
        return {"written": 0}
    invalidate_today()
    ctx = ctx if ctx is not None else _make_context(cfg)
    written = ensure_dynamic_targets_frozen(cfg, ctx, {}, {}, {}, modes=modes, force=True, client=client)
    return {"written": written}

"""Import planner shared by Ore & Minerals and Module Reprocessing (Import tab).

Question: "I can spend X ISK and/or haul Y m3 - which shortlist items, and how
many, earn the most once reprocessed?" Pure, no I/O: the callers price the
items exactly like their own Shortlist and pass in the hub's sell orders.

Rules (confirmed with the user 2026-11-10):
- Supply is the hub's real sell orders, bought cheapest first. An order is
  used only while its landed price still meets the tool's minimum margin and
  minimum profit per unit (`cap_hub_price`), so the most expensive order
  bought still clears the same bar as the Shortlist's "Import" decision.
- Mineral sale value is the Shortlist's (C-J sell price x haircut, refining
  tax deducted). Selling a large output may move C-J's price; the plan only
  reports the output next to the quantity listed at C-J, it does not change
  the selection.

The solve is a *continuous* LP (HiGHS via scipy), rounded down to whole
portions and topped up greedily. Deliberately no integer columns: listed
quantities reach the billions, and large finite integer bounds can make HiGHS
ignore its time limit (see CLAUDE.md "HiGHS can ignore its time limit").
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from scipy.optimize import linprog

from . import hubs
from .market_depth import sell_price_tiers

LIMITED_BY_BUDGET = "budget"
LIMITED_BY_VOLUME = "volume"
LIMITED_BY_SUPPLY = "supply"

ORE_CATEGORY = "ore"
MODULE_CATEGORY = "module"

# LP solutions come back as floats (99.99999998 units); this keeps floor()
# from dropping a whole portion over round-off.
_ROUND_EPS = 1e-6
_MAX_TOP_UP_STEPS = 100_000


class ImportPlanError(ValueError):
    pass


def validate_limits(budget_isk: Optional[float], volume_m3: Optional[float]) -> None:
    if budget_isk is None and volume_m3 is None:
        raise ImportPlanError("Enter an ISK budget, a volume, or both.")
    if (budget_isk is not None and budget_isk <= 0) or (volume_m3 is not None and volume_m3 <= 0):
        raise ImportPlanError("Budget and volume must be greater than 0.")


@dataclass(frozen=True)
class ImportTier:
    """Up to `max_units` at hub price `hub_price`, landed at `unit_cost`."""
    hub_price: float
    unit_cost: float
    max_units: int


@dataclass
class ImportSource:
    type_id: int
    item: str
    category: str                      # "ore" / "module"
    hub_region_id: Optional[int]
    portion_size: int
    volume_m3: float                   # per unit
    net_sell_per_unit: float           # Shortlist net_sell / portion_size
    yield_per_portion: dict[int, int]  # gross minerals of one portion
    tiers: list[ImportTier]
    breakeven_buy_price: Optional[float] = None
    listed_units: Optional[float] = None  # everything listed at the hub
    max_hub_price: Optional[float] = None  # the margin cap used for `tiers`


@dataclass
class ImportLine:
    type_id: int
    item: str
    category: str
    hub_region_id: Optional[int]
    hub_name: str
    units: int
    portions: int
    cost: float
    avg_unit_cost: float
    max_unit_cost: float
    max_hub_price: float
    units_within_cap: int
    listed_units: Optional[float]
    volume_m3: float
    net_value: float
    profit: float
    margin: Optional[float]
    profit_per_m3: Optional[float]
    breakeven_buy_price: Optional[float]
    price_cap: Optional[float]


@dataclass
class ImportMineral:
    type_id: int
    name: str
    quantity: int
    unit_value: Optional[float]
    value: Optional[float]
    home_listed: Optional[float]
    exceeds_listed: bool


@dataclass
class ImportPlan:
    lines: list[ImportLine] = field(default_factory=list)
    minerals: list[ImportMineral] = field(default_factory=list)
    budget_isk: Optional[float] = None
    volume_limit_m3: Optional[float] = None
    total_cost: float = 0.0
    total_volume_m3: float = 0.0
    total_mineral_value: Optional[float] = None
    total_net_value: float = 0.0
    total_profit: float = 0.0
    margin: Optional[float] = None
    limited_by: str = LIMITED_BY_SUPPLY
    candidates: int = 0
    profitable_candidates: int = 0


def cap_hub_price(net_sell_per_unit: float, volume_m3: float, freight_per_m3: float, broker_fee: float,
                  min_margin: float, min_profit: float) -> Optional[float]:
    """Highest hub price at which one unit still meets the tool's Import bar:
    landed <= net / (1 + min_margin) and net - landed > min_profit, with
    landed = price x (1 + broker_fee) + volume x freight (both Shortlists'
    margin = profit / landed cost). None when no price qualifies."""
    limits = [net_sell_per_unit - max(0.0, min_profit)]
    if 1 + min_margin > 0:
        limits.append(net_sell_per_unit / (1 + min_margin))
    landed_max = min(limits)
    price = (landed_max - volume_m3 * freight_per_m3) / (1 + broker_fee)
    return price if price > 0 else None


def build_tiers(orders: list[dict], reference_price: Optional[float], max_hub_price: Optional[float],
                landed: Callable[[float], Optional[float]]) -> list[ImportTier]:
    """The hub's sell orders up to `max_hub_price`, in market_depth's price
    bands, each landed through the caller's own formula."""
    if max_hub_price is None:
        return []
    tiers = []
    for price, units in sell_price_tiers(orders, reference_price, 0.0, max_price=max_hub_price):
        cost = landed(price)
        if cost is not None and units > 0:
            tiers.append(ImportTier(hub_price=price, unit_cost=cost, max_units=int(units)))
    return tiers


def _profitable_tiers(source: ImportSource) -> list[ImportTier]:
    return sorted((t for t in source.tiers if t.max_units > 0 and source.net_sell_per_unit - t.unit_cost > 0),
                  key=lambda t: t.unit_cost)


def _draw(tiers: list[ImportTier], start: int, units: int) -> tuple[float, float, float]:
    """(cost, highest hub price, highest landed unit cost) of `units` units
    after the first `start` units, cheapest tiers first. The caller makes sure
    there is enough supply."""
    cost, top_hub, top_cost, skip, need = 0.0, 0.0, 0.0, start, units
    for tier in tiers:
        if need <= 0:
            break
        if skip >= tier.max_units:
            skip -= tier.max_units
            continue
        take = min(tier.max_units - skip, need)
        skip = 0
        cost += take * tier.unit_cost
        top_hub = max(top_hub, tier.hub_price)
        top_cost = max(top_cost, tier.unit_cost)
        need -= take
    return cost, top_hub, top_cost


def _lp_units(sources: list[ImportSource], tiers_by_source: list[list[ImportTier]],
              budget: Optional[float], volume: Optional[float]) -> list[float]:
    """Continuous LP: units per source maximising profit under the limits."""
    columns: list[tuple[int, ImportTier]] = [(i, t) for i, tiers in enumerate(tiers_by_source) for t in tiers]
    units = [0.0] * len(sources)
    if not columns:
        return units
    c = np.array([-(sources[i].net_sell_per_unit - t.unit_cost) for i, t in columns])
    rows, rhs = [], []
    if budget is not None:
        rows.append([t.unit_cost / budget for _i, t in columns])
        rhs.append(1.0)
    if volume is not None:
        rows.append([sources[i].volume_m3 / volume for i, _t in columns])
        rhs.append(1.0)
    bounds = [(0.0, float(t.max_units)) for _i, t in columns]
    result = linprog(c, A_ub=np.array(rows), b_ub=np.array(rhs), bounds=bounds, method="highs")
    if result.status != 0 or result.x is None:
        raise ImportPlanError(f"The import plan could not be solved ({result.message}).")
    for (i, _t), x in zip(columns, result.x):
        units[i] += max(0.0, float(x))
    return units


def plan_import(sources: list[ImportSource], budget_isk: Optional[float] = None,
                volume_m3: Optional[float] = None,
                mineral_unit_values: Optional[dict[int, Optional[float]]] = None,
                mineral_names: Optional[dict[int, str]] = None,
                home_listed: Optional[dict[int, Optional[float]]] = None) -> ImportPlan:
    """The most profitable buy list within `budget_isk` (landed ISK, broker
    fee and freight included) and/or `volume_m3` (packaged m3 hauled)."""
    validate_limits(budget_isk, volume_m3)
    mineral_unit_values = mineral_unit_values or {}
    mineral_names = mineral_names or {}
    home_listed = home_listed or {}

    tiers_by_source = [_profitable_tiers(s) for s in sources]
    supply = [sum(t.max_units for t in tiers) for tiers in tiers_by_source]
    plan = ImportPlan(budget_isk=budget_isk, volume_limit_m3=volume_m3, candidates=len(sources),
                      profitable_candidates=sum(1 for n, s in zip(supply, sources) if n >= max(1, s.portion_size)))

    lp_units = _lp_units(sources, tiers_by_source, budget_isk, volume_m3)
    portions = [0] * len(sources)
    cost_used = vol_used = 0.0
    for i, s in enumerate(sources):
        step = max(1, s.portion_size)
        p = min(int(math.floor(lp_units[i] / step + _ROUND_EPS)), supply[i] // step)
        if p <= 0:
            continue
        portions[i] = p
        cost_used += _draw(tiers_by_source[i], 0, p * step)[0]
        vol_used += p * step * s.volume_m3

    def remaining_budget() -> float:
        return math.inf if budget_isk is None else budget_isk - cost_used

    def remaining_volume() -> float:
        return math.inf if volume_m3 is None else volume_m3 - vol_used

    def next_portion(i: int) -> Optional[tuple[float, float, float]]:
        """(cost, volume, profit) of source i's next whole portion, or None."""
        s = sources[i]
        step = max(1, s.portion_size)
        start = portions[i] * step
        if start + step > supply[i]:
            return None
        cost = _draw(tiers_by_source[i], start, step)[0]
        profit = s.net_sell_per_unit * step - cost
        if profit <= 0:
            return None
        return cost, step * s.volume_m3, profit

    # Greedy top-up of what flooring left over, best profit per unit of the
    # limits (each normalised to its own size) first.
    for _ in range(_MAX_TOP_UP_STEPS):
        best, best_score = None, 0.0
        rb, rv = remaining_budget(), remaining_volume()
        for i in range(len(sources)):
            nxt = next_portion(i)
            if nxt is None:
                continue
            cost, vol, profit = nxt
            if cost > rb + 1e-6 or vol > rv + 1e-9:
                continue
            weight = ((cost / budget_isk) if budget_isk else 0.0) + ((vol / volume_m3) if volume_m3 else 0.0)
            score = profit / weight if weight > 0 else math.inf
            if best is None or score > best_score:
                best, best_score = i, score
        if best is None:
            break
        cost, vol, _profit = next_portion(best)
        portions[best] += 1
        cost_used += cost
        vol_used += vol

    plan.limited_by = _limited_by(sources, budget_isk, volume_m3, cost_used, vol_used, next_portion)

    minerals: dict[int, int] = {}
    for i, s in enumerate(sources):
        if portions[i] <= 0:
            continue
        step = max(1, s.portion_size)
        units = portions[i] * step
        cost, top_hub, top_cost = _draw(tiers_by_source[i], 0, units)
        net_value = s.net_sell_per_unit * units
        profit = net_value - cost
        volume = units * s.volume_m3
        plan.lines.append(ImportLine(
            type_id=s.type_id, item=s.item, category=s.category, hub_region_id=s.hub_region_id,
            hub_name=hubs.hub_name(s.hub_region_id), units=units, portions=portions[i], cost=cost,
            avg_unit_cost=cost / units, max_unit_cost=top_cost,
            max_hub_price=top_hub, units_within_cap=supply[i], listed_units=s.listed_units, volume_m3=volume,
            net_value=net_value, profit=profit, margin=profit / cost if cost else None,
            profit_per_m3=profit / volume if volume else None, breakeven_buy_price=s.breakeven_buy_price,
            price_cap=s.max_hub_price,
        ))
        for mineral, qty in s.yield_per_portion.items():
            minerals[mineral] = minerals.get(mineral, 0) + qty * portions[i]

    plan.lines.sort(key=lambda line: line.profit, reverse=True)
    plan.total_cost = sum(line.cost for line in plan.lines)
    plan.total_volume_m3 = sum(line.volume_m3 for line in plan.lines)
    plan.total_net_value = sum(line.net_value for line in plan.lines)
    plan.total_profit = plan.total_net_value - plan.total_cost
    plan.margin = plan.total_profit / plan.total_cost if plan.total_cost else None

    all_priced = True
    for mineral, qty in minerals.items():
        unit_value = mineral_unit_values.get(mineral)
        listed = home_listed.get(mineral)
        if unit_value is None:
            all_priced = False
        plan.minerals.append(ImportMineral(
            type_id=mineral, name=mineral_names.get(mineral, str(mineral)), quantity=qty, unit_value=unit_value,
            value=qty * unit_value if unit_value is not None else None, home_listed=listed,
            exceeds_listed=listed is not None and qty > listed,
        ))
    plan.minerals.sort(key=lambda m: (m.value is None, -(m.value or 0.0), m.name))
    if plan.minerals and all_priced:
        plan.total_mineral_value = sum(m.value for m in plan.minerals)
    elif not plan.minerals:
        plan.total_mineral_value = 0.0
    return plan


def _limited_by(sources, budget, volume, cost_used, vol_used, next_portion) -> str:
    """Which limit stopped the plan: `supply` when no profitable whole
    portion is left anywhere; otherwise the given limit that is used the most."""
    if not any(next_portion(i) is not None for i in range(len(sources))):
        return LIMITED_BY_SUPPLY
    usage = []
    if budget is not None:
        usage.append((cost_used / budget, LIMITED_BY_BUDGET))
    if volume is not None:
        usage.append((vol_used / volume, LIMITED_BY_VOLUME))
    return max(usage)[1]

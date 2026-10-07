"""Production demand view (F1, docs/PI_PLAN.md 3.6) - make via PI vs. buy.

Source is Production's latest buy list (`production_buy_list`, quantities
from the last plan run, read via storage like Sorting does). Production has
no daily demand figure and its in-process plan cache is lost on restart, so
this view is quantity-based and never invents a daily rate (P-39).

For each PI commodity on the buy list, the cheapest PI way to make it is
costed per unit (inputs at market, taxes, freight, setup share) and compared
with buying it. Pure apart from the callables passed in.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

from . import economics as econ
from .model import CHAINS, Evaluation, StaticData

# best(chain, product) -> list of (evaluation) candidates for that chain
# (one per planet type for extraction chains, one for factory chains).
CandidatesFn = Callable[[str, int], list[Evaluation]]


def unit_cost(ev: Evaluation, e: econ.Economics) -> Optional[float]:
    """PI cost per unit of the product: every cost of the colony, minus what
    its priced by-products actually sell for, divided by the product output.
    By-product credit is their own revenue (`byproduct_revenue_per_day`), not
    a share of colony revenue weighted by unit count - a few expensive units
    next to a large pile of cheap surplus would otherwise look free."""
    units = e.output_units_per_day
    if units <= 0:
        return None
    costs = e.input_cost_per_day + e.export_tax_per_day + e.import_tax_per_day + e.freight_per_day + e.setup_per_day
    return (costs - e.byproduct_revenue_per_day) / units


def demand_rows(static: StaticData, buy_list: dict[int, float], prices: econ.Prices,
                market: econ.MarketSettings, candidates: CandidatesFn, days: float) -> list[dict]:
    rows = []
    for type_id, qty in sorted(buy_list.items()):
        c = static.commodities.get(type_id)
        if c is None or c.tier == 0 or qty <= 0:
            continue
        buy_price = econ.input_unit_cost(type_id, prices, market)
        best: Optional[tuple[float, Evaluation, econ.Economics]] = None
        for chain, (_source, target) in CHAINS.items():
            if target != c.tier:
                continue
            for ev in candidates(chain, type_id):
                e = econ.compute(ev, static, prices, market)
                cost = unit_cost(ev, e)
                if cost is None or e.missing_prices:
                    continue
                if best is None or cost < best[0]:
                    best = (cost, ev, e)
        row = {
            "type_id": type_id, "name": c.name, "tier": c.tier, "quantity": qty,
            "buy_price": buy_price,
            "buy_total": qty * buy_price if buy_price is not None else None,
        }
        if best is None:
            row.update({"pi_unit_cost": None, "chain": None, "saving": None, "colonies_needed": None,
                        "make_via_pi": False})
        else:
            cost, ev, e = best
            per_colony_day = e.output_units_per_day
            row.update({
                "pi_unit_cost": cost,
                "chain": ev.design.chain,
                "planet_type_id": ev.design.planet_type_id,
                "pi_total": qty * cost,
                "saving": (qty * (buy_price - cost)) if buy_price is not None else None,
                "colonies_needed": (qty / (per_colony_day * days)) if per_colony_day > 0 else None,
                "colonies_needed_ceil": math.ceil(qty / (per_colony_day * days) - 1e-9) if per_colony_day > 0 else None,
                "make_via_pi": buy_price is not None and cost < buy_price,
            })
        rows.append(row)
    rows.sort(key=lambda r: -(r.get("saving") or 0))
    return rows

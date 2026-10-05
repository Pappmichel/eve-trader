"""Phase 5c design tools - pure (docs/PI_PLAN.md 6A.2):

- ways_to_build: every split of a product's inputs into "made on this
  planet" and "hauled in", each searched for its best design;
- partial sourcing for P0->P2: extract one P1's raw material and haul the
  other P1 in (ways_to_build with an extraction input);
- mixed_p2: a different P2 per Advanced facility on a P1->P2 colony;
- storage_suggestion: the smallest fix when storage runs out before the
  collection interval (add storage, else trade production for storage,
  else the same product from the tier above);
- grow_to_supply: add factories while the supply feeds them and the budget
  holds (e.g. after raising the yield setting).

The engine already treats every type a design has factories for as made on
the planet and everything else as extracted or hauled in, so a variant is
just a different set of factories.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import replace
from typing import Optional

from . import constants as C
from .engine import (
    EPS, MAX_FACTORIES_PER_STAGE, MAX_LAUNCHPADS, MAX_STORAGES, RATE_STEPS_PER_FACTORY, Assumptions, _better,
    evaluate, factories_for_rate, made_in,
)
from .model import CHAINS, Design, Evaluation, Planet, StaticData, factory_kind_for_tier

CUSTOM_CHAIN = {2: "P1-P2", 3: "P2-P3", 4: "P3-P4"}


def _kind_ok(static: StaticData, planet_type_id: int, t: int) -> bool:
    spec = static.structure(factory_kind_for_tier(static.tier(t) or 0), planet_type_id)
    s = static.schematic_by_output.get(t)
    return spec is not None and s is not None and spec.type_id in s.pin_type_ids


def _search_made(static: StaticData, planet: Planet, product: int, made: frozenset, cc_level: int,
                 assumptions: Assumptions, chain: str) -> Optional[Evaluation]:
    """Best design that makes exactly `made` (plus the product) and hauls in
    the rest - stepped target rate, pads/storage like the engine search."""
    pt = planet.planet_type_id
    top = static.schematic_by_output[product]
    per_top = top.output_qty * top.runs_per_hour
    best: Optional[Evaluation] = None
    for pads in range(1, MAX_LAUNCHPADS + 1):
        for storages in range(0, MAX_STORAGES + 1):
            seen: set = set()
            for m in range(1, MAX_FACTORIES_PER_STAGE * RATE_STEPS_PER_FACTORY + 1):
                counts = factories_for_rate(static, product, 0, per_top * m / RATE_STEPS_PER_FACTORY, made)
                factories = tuple(sorted(counts.items()))
                if factories in seen:
                    continue
                seen.add(factories)
                ev = evaluate(static, planet, Design(chain, product, pt, cc_level, factories, (), pads, storages),
                              assumptions)
                if not ev.fits:
                    break
                best = _better(best, ev)
    return best


def ways_to_build(static: StaticData, planet: Planet, product: int, cc_level: int,
                  assumptions: Assumptions) -> list[dict]:
    """Every split of the product's direct inputs (tier >= 2; a P1 cannot be
    made without its P0) into made-here and hauled-in, one level deep, each
    with its best design. Sorted by effective output per hour."""
    tier = static.tier(product) or 0
    if tier < 2 or not _kind_ok(static, planet.planet_type_id, product):
        return []
    s = static.schematic_by_output[product]
    makeable = [i for i, _q in s.inputs if (static.tier(i) or 0) >= 2 and _kind_ok(static, planet.planet_type_id, i)]
    chain = CUSTOM_CHAIN[tier]
    out = []
    for k in range(len(makeable) + 1):
        for subset in itertools.combinations(makeable, k):
            made = frozenset(subset) | {product}
            ev = _search_made(static, planet, product, made, cc_level, assumptions, chain)
            out.append({
                "made": sorted(made, key=lambda t: (static.tier(t) or 0, t)),
                "hauled": sorted({i for t in made for i, _q in static.schematic_by_output[t].inputs} - made),
                "evaluation": ev,
            })
    out.sort(key=lambda v: -(v["evaluation"].effective_product_per_hour if v["evaluation"] else -1))
    return out


def partial_p0_p2(static: StaticData, planet: Planet, product: int, cc_level: int,
                  assumptions: Assumptions) -> list[dict]:
    """P0->P2 with one P1 extracted on the planet and the other hauled in
    (for each P1 whose P0 the planet carries), best design each."""
    if (static.tier(product) or 0) != 2:
        return []
    s2 = static.schematic_by_output[product]
    resources = C.PLANET_RESOURCES.get(planet.planet_type_id, frozenset())
    out = []
    for p1, _q in s2.inputs:
        p0 = static.schematic_by_output[p1].inputs[0][0]
        if p0 not in resources:
            continue
        best: Optional[Evaluation] = None
        s1 = static.schematic_by_output[p1]
        per_basic = s1.inputs[0][1] * s1.runs_per_hour
        y = assumptions.effective_yield
        for heads in range(1, C.MAX_EXTRACTOR_HEADS + 1):
            supply = heads * y
            for basics in sorted({max(1, math.floor(supply / per_basic)), max(1, math.ceil(supply / per_basic - EPS))}):
                p1_made = min(basics * s1.output_qty * s1.runs_per_hour, supply / s1.inputs[0][1] * s1.output_qty)
                need = dict(s2.inputs)[p1] * s2.runs_per_hour
                for adv in sorted({max(1, math.floor(p1_made / need)), max(1, math.ceil(p1_made / need - EPS))}):
                    for pads in (1, 2):
                        d = Design("P0-P2", product, planet.planet_type_id, cc_level,
                                   tuple(sorted({product: adv, p1: basics}.items())), ((p0, heads),), pads, 0)
                        best = _better(best, evaluate(static, planet, d, assumptions))
        out.append({"extracted_p1": p1, "hauled": [i for i, _q in s2.inputs if i != p1], "evaluation": best})
    return out


def mixed_p2(static: StaticData, planet: Planet, assignments: dict[int, int], cc_level: int,
             assumptions: Assumptions, launchpads: int = 1, storages: int = 0) -> Evaluation:
    """A P1->P2 colony with a different P2 per Advanced facility
    (`assignments`: P2 type id -> number of facilities)."""
    if not assignments or any((static.tier(t) or 0) != 2 or n < 0 for t, n in assignments.items()):
        raise ValueError("Assign P2 products (with counts) to the Advanced facilities")
    product = max(assignments, key=lambda t: (assignments[t], -t))
    d = Design("P1-P2", product, planet.planet_type_id, cc_level,
               tuple(sorted((t, n) for t, n in assignments.items() if n > 0)), (), launchpads, storages)
    return evaluate(static, planet, d, assumptions)


def storage_suggestion(static: StaticData, planet: Planet, design: Design, assumptions: Assumptions) -> dict:
    """Smallest change that lets the colony last the collection interval."""
    ev = evaluate(static, planet, design, assumptions)
    interval = assumptions.interval_hours
    if math.isinf(ev.buffer_hours) or ev.buffer_hours >= interval:
        return {"kind": "covered", "buffer_hours": None if math.isinf(ev.buffer_hours) else ev.buffer_hours}
    # 1. add storage facilities
    best_partial = None
    for extra in range(1, MAX_STORAGES + 1):
        d = replace(design, storages=design.storages + extra)
        e = evaluate(static, planet, d, assumptions)
        if not e.fits:
            break
        best_partial = (extra, e)
        if e.buffer_hours >= interval:
            return {"kind": "add_storage", "storages": extra, "buffer_hours": e.buffer_hours,
                    "design": d.to_dict(), "reaches_interval": True}
    if best_partial is not None:
        extra, e = best_partial
        return {"kind": "add_storage", "storages": extra, "buffer_hours": e.buffer_hours,
                "design": replace(design, storages=design.storages + extra).to_dict(), "reaches_interval": False}
    # 2. trade the fewest production units for storage
    made = made_in(static, design)
    top = design.product_type_id
    facs = dict(design.factories)
    for remove in range(1, facs.get(top, 0)):
        trimmed = dict(facs)
        trimmed[top] -= remove
        for extra in range(1, MAX_STORAGES + 1):
            d = replace(design, factories=tuple(sorted(trimmed.items())), storages=design.storages + extra)
            e = evaluate(static, planet, d, assumptions)
            if e.fits and e.buffer_hours >= interval:
                share = e.product_per_hour / ev.product_per_hour if ev.product_per_hour else 0.0
                return {"kind": "trade", "removed_factories": remove, "storages": extra,
                        "output_share": share, "buffer_hours": e.buffer_hours, "design": d.to_dict()}
    # 3. same product from the tier above
    source, target = CHAINS.get(design.chain, (None, None))
    if source is not None and source + 1 < target:
        higher = next((name for name, st in CHAINS.items() if st == (source + 1, target)), None)
        if higher:
            return {"kind": "higher_tier", "chain": higher}
    _ = made
    return {"kind": "none", "buffer_hours": ev.buffer_hours}


def grow_to_supply(static: StaticData, planet: Planet, design: Design, assumptions: Assumptions,
                   max_steps: int = 60) -> Evaluation:
    """Add one factory at a time (to whichever made type helps most) while
    the effective output rises and the design still fits."""
    current = evaluate(static, planet, design, assumptions)
    for _ in range(max_steps):
        best_next = None
        for t, n in current.design.factories:
            facs = dict(current.design.factories)
            facs[t] = n + 1
            e = evaluate(static, planet, replace(current.design, factories=tuple(sorted(facs.items()))), assumptions)
            if not e.fits or e.effective_product_per_hour <= current.effective_product_per_hour + 1e-9:
                continue
            if best_next is None or e.effective_product_per_hour > best_next.effective_product_per_hour:
                best_next = e
        if best_next is None:
            break
        current = best_next
    return current

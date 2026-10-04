"""PI capacity, throughput and best-design search - pure, no I/O.

docs/PI_PLAN.md 3.2 / docs/PI_TECHNICAL_DESIGN.md 3.3-3.4. A design is a
set of structure counts; `evaluate` turns it into CPU/power use, steady-state
rates, storage duration and setup cost for one planet; `best_design` searches
the integer space for the design with the highest *effective* output - the
raw output scaled by how long the colony's storage lasts against the
collection interval (a colony whose pads are full after 20 h produces
nothing for the rest of a 48 h interval).

Link costs are estimated here (structures - 1 links of a typical length);
the layout generator later replaces them with exact per-link figures.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Iterable, Optional

from . import constants as C
from . import decay
from . import static as static_mod
from .model import (
    CHAIN_P0_P1, CHAINS, EXTRACTION_CHAINS, Design, Evaluation, Planet, StaticData, factory_kind_for_tier,
)

# Average link length in multiples of the minimum spacing. The generator's
# lattice spacing is 1.05 x 0.012 rad and most links span one cell, a few
# two (Eve-PI measured +1..+6 extra spacings per colony). 1.15 is the
# planning estimate until a generated layout gives the exact figure.
SPACING_MARGIN = 1.05
LINK_SPAN_FACTOR = 1.15
MAX_LINK_LEVEL = 5
MAX_LAUNCHPADS = 4
MAX_STORAGES = 3
MAX_FACTORIES_PER_STAGE = 60
EPS = 1e-9


class DesignError(ValueError):
    """A chain/product/planet combination that cannot exist (wrong tier,
    missing P0, no High-Tech facility on this planet type...)."""


# ------------------------------------------------------------- recipe tree
def made_types(static: StaticData, product: int, source_tier: int) -> list[int]:
    """Every type the colony manufactures for `product`, lowest tier first.
    Inputs at or below `source_tier` are raw for the colony (extracted for a
    P0 chain, hauled in otherwise)."""
    seen: dict[int, int] = {}

    def walk(t: int) -> None:
        tier = static.tier(t)
        if tier is None or tier <= source_tier or t in seen:
            return
        seen[t] = tier
        s = static.schematic_by_output.get(t)
        if s is None:
            raise DesignError(f"No schematic produces {static.name(t)}")
        for i, _q in s.inputs:
            walk(i)

    walk(product)
    return sorted(seen, key=lambda t: (seen[t], t))


def raw_inputs(static: StaticData, product: int, source_tier: int) -> list[int]:
    """Inputs the colony does not make itself (P0 to extract, or imports)."""
    made = set(made_types(static, product, source_tier))
    raw: set[int] = set()
    for t in made:
        for i, _q in static.schematic_by_output[t].inputs:
            if i not in made:
                raw.add(i)
    return sorted(raw)


def balanced_factories(static: StaticData, product: int, source_tier: int, top_count: int) -> dict[int, int]:
    """Factory counts per made type so that `top_count` factories of the
    product never wait for an on-planet intermediate (rounded up)."""
    made = made_types(static, product, source_tier)
    counts: dict[int, int] = {product: top_count}
    demand: dict[int, float] = {}
    for t in reversed(made):  # highest tier first
        s = static.schematic_by_output[t]
        if t != product:
            per_factory = s.output_qty * s.runs_per_hour
            counts[t] = max(1, math.ceil(demand.get(t, 0.0) / per_factory - EPS)) if demand.get(t, 0.0) > 0 else 0
        runs = counts[t] * s.runs_per_hour
        for i, q in s.inputs:
            demand[i] = demand.get(i, 0.0) + runs * q
    return {t: n for t, n in counts.items() if n > 0}


def factories_for_rate(static: StaticData, product: int, source_tier: int, rate: float) -> dict[int, int]:
    """Factory counts to make `rate` units/h of the product, each stage
    rounded up to whole factories and sized to what the stage above really
    needs. Unlike balanced_factories this allows a partly used top stage -
    real P1->P4 colonies run their High-Tech plant below capacity, since a
    fully fed one needs ~30 factories and never fits a Command Center."""
    made = made_types(static, product, source_tier)
    demand: dict[int, float] = {product: rate}
    counts: dict[int, int] = {}
    for t in reversed(made):  # highest tier first
        s = static.schematic_by_output[t]
        d = demand.get(t, 0.0)
        if d <= EPS:
            continue
        counts[t] = max(1, math.ceil(d / (s.output_qty * s.runs_per_hour) - EPS))
        runs_needed = d / s.output_qty
        for i, q in s.inputs:
            demand[i] = demand.get(i, 0.0) + runs_needed * q
    return counts


# ---------------------------------------------------------------- evaluate
@dataclass(frozen=True)
class Assumptions:
    """Everything besides the design itself that changes the result."""
    yield_per_head: float = 2000.0        # P0/head/h at the reference program length
    program_hours: float = C.REFERENCE_PROGRAM_HOURS
    interval_hours: float = 24.0          # collection interval

    @property
    def effective_yield(self) -> float:
        return self.yield_per_head * decay.program_ratio(self.program_hours)


def link_length_km(radius_km: float) -> float:
    return radius_km * C.MIN_PIN_SEPARATION_RAD * SPACING_MARGIN * LINK_SPAN_FACTOR


def cc_setup_isk(static: StaticData, planet_type_id: int, cc_level: int) -> float:
    cc = static.structure(C.KIND_COMMAND_CENTER, planet_type_id)
    upgrades = sum(C.CC_LEVELS[lv][2] for lv in range(1, cc_level + 1))
    return upgrades + (cc.isk_cost if cc else 0.0)


def evaluate(static: StaticData, planet: Planet, design: Design, assumptions: Assumptions) -> Evaluation:
    source_tier, _target = CHAINS[design.chain]
    pt = planet.planet_type_id
    notes: list[str] = []
    cpu_cap, power_cap, _ = C.CC_LEVELS[design.cc_level]
    structures_ok = True

    # --- supply of raw inputs
    extracted: dict[int, float] = {}
    if design.chain in EXTRACTION_CHAINS:
        y = assumptions.effective_yield
        for p0, heads in design.ecus:
            extracted[p0] = extracted.get(p0, 0.0) + heads * y
    made = made_types(static, design.product_type_id, source_tier)
    counts = dict(design.factories)

    # --- full-run needs, then bottom-up utilisation with proportional sharing
    full_need: dict[int, float] = {}
    for t in made:
        s = static.schematic_by_output[t]
        runs = counts.get(t, 0) * s.runs_per_hour
        for i, q in s.inputs:
            full_need[i] = full_need.get(i, 0.0) + runs * q

    available: dict[int, float] = dict(extracted)
    imported_raw = source_tier >= 1
    produced: dict[int, float] = {}
    consumed: dict[int, float] = {}
    utilization: dict[int, float] = {}
    for t in made:
        s = static.schematic_by_output[t]
        n = counts.get(t, 0)
        runs = n * s.runs_per_hour
        if runs <= 0:
            utilization[t] = 0.0
            produced[t] = 0.0
            continue
        u = 1.0
        for i, q in s.inputs:
            need = runs * q
            raw = static.tier(i) is not None and static.tier(i) <= source_tier
            if raw and imported_raw:
                continue  # hauled in as needed
            share = available.get(i, 0.0) * (need / full_need[i]) if full_need.get(i) else 0.0
            u = min(u, share / need if need > 0 else 1.0)
        u = max(0.0, min(1.0, u))
        utilization[t] = u
        produced[t] = runs * s.output_qty * u
        for i, q in s.inputs:
            consumed[i] = consumed.get(i, 0.0) + runs * q * u
        available[t] = available.get(t, 0.0) + produced[t]

    imports: dict[int, float] = {}
    exports: dict[int, float] = {}
    for i, used in consumed.items():
        tier = static.tier(i)
        if tier is not None and tier <= source_tier and imported_raw:
            imports[i] = used
    for t in made:
        surplus = produced.get(t, 0.0) - consumed.get(t, 0.0)
        if surplus > EPS:
            exports[t] = surplus
    for p0, amount in extracted.items():
        surplus = amount - consumed.get(p0, 0.0)
        if surplus > EPS:
            exports[p0] = surplus

    def vol(t: int) -> float:
        c = static.commodities.get(t)
        return c.volume if c else 0.0

    import_m3 = sum(q * vol(t) for t, q in imports.items())
    export_m3 = sum(q * vol(t) for t, q in exports.items())

    # --- structures, CPU/power, storage
    lp = static.structure(C.KIND_LAUNCHPAD, pt)
    st = static.structure(C.KIND_STORAGE, pt)
    ecu = static.structure(C.KIND_ECU, pt)
    if lp is None or (design.storages and st is None):
        structures_ok = False
        notes.append("missing launchpad/storage structure data for this planet type")
    cpu = power = 0.0
    setup = cc_setup_isk(static, pt, design.cc_level)
    for t, n in design.factories:
        tier = static.tier(t) or 0
        spec = static.structure(factory_kind_for_tier(tier), pt)
        schematic = static.schematic_by_output.get(t)
        if spec is None or schematic is None or spec.type_id not in schematic.pin_type_ids:
            structures_ok = False
            notes.append(f"{static.name(t)} cannot be made on this planet type")
            continue
        cpu += n * spec.cpu
        power += n * spec.power
        setup += n * spec.isk_cost
    if lp is not None:
        cpu += design.launchpads * lp.cpu
        power += design.launchpads * lp.power
        setup += design.launchpads * lp.isk_cost
    if st is not None and design.storages:
        cpu += design.storages * st.cpu
        power += design.storages * st.power
        setup += design.storages * st.isk_cost
    resources = C.PLANET_RESOURCES.get(pt, frozenset())
    for p0, heads in design.ecus:
        if ecu is None or p0 not in resources or not (1 <= heads <= C.MAX_EXTRACTOR_HEADS):
            structures_ok = False
            notes.append(f"cannot extract {static.name(p0)} with {heads} heads here")
            continue
        cpu += ecu.cpu + heads * static.head_cpu
        power += ecu.power + heads * static.head_power
        setup += ecu.isk_cost

    # --- links: a tree over all pins; the links next to the hubs carry the
    # traffic and get upgraded when it exceeds their capacity.
    pins = design.pin_count
    link_count = max(0, pins - 1)
    km = link_length_km(planet.radius_km)
    internal_m3 = sum(consumed.get(t, 0.0) * vol(t) for t in consumed if t not in imports)
    hub_traffic = import_m3 + export_m3 + 2 * internal_m3
    hubs = design.launchpads + design.storages
    arms = max(1, min(6 * max(1, hubs), link_count)) if link_count else 0
    level = 0
    if arms:
        load = hub_traffic / arms
        while level < MAX_LINK_LEVEL and static.link.capacity_at(level) < load:
            level += 1
        if static.link.capacity_at(level) < load:
            notes.append("link bandwidth exceeded even at the highest upgrade level")
    upgraded = arms if level > 0 else 0
    c0, p0_ = static.link.cost(km, 0)
    c1, p1 = static.link.cost(km, level)
    link_cpu = (link_count - upgraded) * c0 + upgraded * c1
    link_power = (link_count - upgraded) * p0_ + upgraded * p1
    cpu += link_cpu
    power += link_power

    capacity = (design.launchpads * (lp.capacity if lp else 0.0)
                + design.storages * (st.capacity if st else 0.0))
    denom = max(import_m3, export_m3)
    buffer_hours = capacity / denom if denom > EPS else math.inf
    effective = 1.0 if math.isinf(buffer_hours) else min(1.0, buffer_hours / max(assumptions.interval_hours, EPS))

    idle = sum(counts.get(t, 0) * (1.0 - utilization.get(t, 0.0)) for t in made)
    cpu_i, power_i = int(math.ceil(cpu - EPS)), int(math.ceil(power - EPS))
    fits = structures_ok and cpu_i <= cpu_cap and power_i <= power_cap
    return Evaluation(
        design=design, fits=fits, cpu_used=cpu_i, power_used=power_i,
        cpu_capacity=cpu_cap, power_capacity=power_cap,
        link_count=link_count, link_km=km, link_level=level, link_cpu=link_cpu, link_power=link_power,
        extracted=extracted, produced=produced, consumed=consumed, imports=imports, exports=exports,
        product_per_hour=produced.get(design.product_type_id, 0.0),
        effective_factor=effective, buffer_hours=buffer_hours,
        import_m3_per_hour=import_m3, export_m3_per_hour=export_m3,
        utilization=utilization, idle_factories=idle, setup_isk=setup, notes=tuple(dict.fromkeys(notes)),
    )


# ---------------------------------------------------------------- feasibility
def check_feasible(static: StaticData, planet_type_id: int, chain: str, product: int) -> Optional[str]:
    """None if the chain can exist on this planet type, else the reason."""
    if chain not in CHAINS:
        return f"Unknown chain {chain}"
    source_tier, target_tier = CHAINS[chain]
    tier = static.tier(product)
    if tier != target_tier:
        return f"{static.name(product)} is not a P{target_tier} product"
    try:
        made = made_types(static, product, source_tier)
    except DesignError as e:
        return str(e)
    for t in made:
        kind = factory_kind_for_tier(static.tier(t) or 0)
        spec = static.structure(kind, planet_type_id)
        if spec is None or spec.type_id not in static.schematic_by_output[t].pin_type_ids:
            if kind == C.KIND_HIGH_TECH:
                return "P4 needs a High-Tech Production Plant (Barren or Temperate only)"
            return f"{static.name(t)} cannot be made on this planet type"
    if source_tier == 0:
        missing = [p for p in raw_inputs(static, product, 0) if p not in C.PLANET_RESOURCES.get(planet_type_id, ())]
        if missing:
            return "Planet type lacks " + ", ".join(static.name(p) for p in missing)
    return None


# ---------------------------------------------------------------- search
def _key(ev: Evaluation) -> tuple:
    """Higher is better: effective output, then fewer idle factories, then
    fewer structures, then cheaper, then more buffer (stable tie-break, P-29)."""
    return (
        round(ev.effective_product_per_hour, 6),
        -round(ev.idle_factories, 6),
        -ev.design.pin_count,
        -round(ev.setup_isk, 0),
        round(min(ev.buffer_hours, 1e6), 3),
        ev.design.to_dict().__repr__(),
    )


def _better(a: Optional[Evaluation], b: Evaluation) -> Evaluation:
    if not b.fits:
        return a  # type: ignore[return-value]
    if a is None or _key(b) > _key(a):
        return b
    return a


# Target output is stepped in quarters of one top-stage factory, so a
# multi-stage chain can run its top factory partly fed (see factories_for_rate).
RATE_STEPS_PER_FACTORY = 4


def _factory_designs(static, planet, chain, product, cc_level, assumptions) -> Iterable[Evaluation]:
    source_tier, _ = CHAINS[chain]
    pt = planet.planet_type_id
    top = static.schematic_by_output[product]
    per_top = top.output_qty * top.runs_per_hour
    for pads in range(1, MAX_LAUNCHPADS + 1):
        for storages in range(0, MAX_STORAGES + 1):
            seen: set[tuple] = set()
            for m in range(1, MAX_FACTORIES_PER_STAGE * RATE_STEPS_PER_FACTORY + 1):
                counts = factories_for_rate(static, product, source_tier, per_top * m / RATE_STEPS_PER_FACTORY)
                factories = tuple(sorted(counts.items()))
                if factories in seen:
                    continue
                seen.add(factories)
                design = Design(chain, product, pt, cc_level, factories, (), pads, storages)
                ev = evaluate(static, planet, design, assumptions)
                if not ev.fits:
                    break
                yield ev


def _extraction_designs(static, planet, chain, product, cc_level, assumptions) -> Iterable[Evaluation]:
    pt = planet.planet_type_id
    p0s = raw_inputs(static, product, 0)
    y = assumptions.effective_yield
    if chain == CHAIN_P0_P1:
        (p0,) = p0s
        s = static.schematic_by_output[product]
        p0_per_factory = s.inputs[0][1] * s.runs_per_hour
        for ecus in (1, 2):
            for heads in range(1, C.MAX_EXTRACTOR_HEADS + 1):
                supply = ecus * heads * y
                bal = max(1, math.ceil(supply / p0_per_factory - EPS))
                for pads in (1, 2):
                    for storages in (0, 1, 2):
                        for n in sorted({max(1, bal - 2), max(1, bal - 1), bal, bal + 1}):
                            design = Design(chain, product, pt, cc_level, ((product, n),),
                                            tuple((p0, heads) for _ in range(ecus)), pads, storages)
                            yield evaluate(static, planet, design, assumptions)
        return
    # P0 -> P2: one ECU per P0, Basic facilities per P1, Advanced on top.
    s2 = static.schematic_by_output[product]
    p1s = [i for i, _q in s2.inputs]
    p1_need = {i: q * s2.runs_per_hour for i, q in s2.inputs}  # per Advanced facility
    p0_of = {p1: static.schematic_by_output[p1].inputs[0][0] for p1 in p1s}
    s1 = {p1: static.schematic_by_output[p1] for p1 in p1s}
    for h1 in range(1, C.MAX_EXTRACTOR_HEADS + 1):
        for h2 in range(1, C.MAX_EXTRACTOR_HEADS + 1):
            heads = dict(zip(p1s, (h1, h2)))
            options: dict[int, list[int]] = {}
            for p1 in p1s:
                per_basic = s1[p1].inputs[0][1] * s1[p1].runs_per_hour
                supply = heads[p1] * y
                base = supply / per_basic
                options[p1] = sorted({max(1, math.floor(base)), max(1, math.ceil(base - EPS))})
            for b1 in options[p1s[0]]:
                for b2 in options[p1s[1]]:
                    basics = dict(zip(p1s, (b1, b2)))
                    p1_made = {
                        p1: min(basics[p1] * s1[p1].output_qty * s1[p1].runs_per_hour,
                                heads[p1] * y / s1[p1].inputs[0][1] * s1[p1].output_qty)
                        for p1 in p1s
                    }
                    a_base = min(p1_made[p1] / p1_need[p1] for p1 in p1s)
                    for a in sorted({max(1, math.floor(a_base)), max(1, math.ceil(a_base - EPS))}):
                        factories = tuple(sorted([(product, a)] + [(p1, basics[p1]) for p1 in p1s]))
                        ecus = tuple((p0_of[p1], heads[p1]) for p1 in p1s)
                        for pads in (1, 2):
                            for storages in (0, 1):
                                design = Design(chain, product, pt, cc_level, factories, ecus, pads, storages)
                                yield evaluate(static, planet, design, assumptions)



def search_best(static: StaticData, planet: Planet, chain: str, product: int, cc_level: int,
                assumptions: Assumptions) -> tuple[Optional[Evaluation], Optional[str]]:
    """(best evaluation, None) or (None, reason it cannot be built)."""
    reason = check_feasible(static, planet.planet_type_id, chain, product)
    if reason:
        return None, reason
    gen = (_extraction_designs if chain in EXTRACTION_CHAINS else _factory_designs)(
        static, planet, chain, product, cc_level, assumptions)
    best: Optional[Evaluation] = None
    for ev in gen:
        best = _better(best, ev)
    if best is None:
        return None, "Nothing fits on this Command Center level and planet size"
    return best, None


# ------------------------------------------------------------- design cache
_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[Optional[Evaluation], Optional[str]]] = {}
_CACHE_MAX = 4096


def radius_bucket(radius_km: float) -> float:
    """Profitability rows use bucketed radii so the cache stays bounded
    (P-30); concrete planets pass `exact_radius=True`."""
    step = 250.0 if radius_km <= 10000 else 2500.0
    return max(step, round(radius_km / step) * step)


def best_design(static: StaticData, planet: Planet, chain: str, product: int, cc_level: int,
                assumptions: Assumptions, exact_radius: bool = False) -> tuple[Optional[Evaluation], Optional[str]]:
    radius = planet.radius_km if exact_radius else radius_bucket(planet.radius_km)
    key = (id(static), planet.planet_type_id, round(radius, 3), chain, product, cc_level,
           round(assumptions.yield_per_head, 3), round(assumptions.program_hours, 3),
           round(assumptions.interval_hours, 3))
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    p = Planet(planet.planet_type_id, radius, planet.planet_id, planet.name, planet.solar_system_id)
    result = search_best(static, p, chain, product, cc_level, assumptions)
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = result
    return result


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


static_mod.on_invalidate(clear_cache)

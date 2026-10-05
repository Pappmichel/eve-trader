"""The one validator/analyser for PI layouts - used by the template library,
the generator (6A pipeline step 6) and the editor. Pure (needs StaticData).

Checks (docs/PI_TECHNICAL_DESIGN.md 3.2/3.3/4): structure ids known; CPU and
power incl. every link at its real length and level (ceil per link, V-5);
minimum spacing 0.012 rad (after the 5-decimal rounding templates use,
P-25); routes <= 7 structures, along existing links, P0 never through a
Basic facility; link bandwidth; High-Tech only where it exists. Throughput
is a small LP (P-26): factories run at most once per cycle, inputs only
over their own routes (route quantity per destination cycle caps them),
hubs can import anything a route takes out of them.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .. import constants as C
from ..model import StaticData, StructureSpec
from .geometry import central_angle
from .template_io import Layout

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    pins: tuple[int, ...] = ()
    route: Optional[int] = None
    link: Optional[int] = None

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message,
                "pins": list(self.pins), "route": self.route, "link": self.link}


@dataclass
class Analysis:
    findings: list[Finding] = field(default_factory=list)
    kinds: list[Optional[str]] = field(default_factory=list)          # per pin
    cpu_used: int = 0
    power_used: int = 0
    cpu_capacity: int = 0
    power_capacity: int = 0
    link_km: list[float] = field(default_factory=list)                # per link
    link_load_m3h: list[float] = field(default_factory=list)          # per link
    link_capacity_m3h: list[float] = field(default_factory=list)
    link_cpu: int = 0
    link_power: int = 0
    runs_per_hour: dict[int, float] = field(default_factory=dict)     # pin index (1-based) -> runs/h
    max_runs_per_hour: dict[int, float] = field(default_factory=dict)
    extracted: dict[int, float] = field(default_factory=dict)         # type -> units/h
    produced: dict[int, float] = field(default_factory=dict)
    consumed: dict[int, float] = field(default_factory=dict)
    imports: dict[int, float] = field(default_factory=dict)
    exports: dict[int, float] = field(default_factory=dict)
    storage_m3: float = 0.0
    buffer_hours: Optional[float] = None
    import_m3_per_hour: float = 0.0
    export_m3_per_hour: float = 0.0
    product_type_id: Optional[int] = None
    chain: Optional[str] = None
    setup_isk: float = 0.0

    @property
    def ok(self) -> bool:
        return not any(f.severity == ERROR for f in self.findings)

    def add(self, severity: str, code: str, message: str, **kw) -> None:
        self.findings.append(Finding(severity, code, message, **kw))


def _round5(x: float) -> float:
    return round(x, 5)


def analyse(static: StaticData, layout: Layout, radius_km: Optional[float] = None,
            yield_per_head: float = 2000.0) -> Analysis:
    """Full analysis. `radius_km` = the planet the colony is checked for;
    without it, half the template's own Diam (P-23)."""
    a = Analysis()
    radius = radius_km if radius_km else (layout.diameter_km / 2.0 if layout.diameter_km else 5000.0)
    if not radius_km:
        a.add(INFO, "radius_from_template", f"No planet chosen - link costs use the template's own radius ({radius:,.0f} km).")
    if layout.cc_level not in C.CC_LEVELS:
        a.add(ERROR, "cc_level", f"Command Center level {layout.cc_level} does not exist (0-5)")
        cc_cpu, cc_power = 0, 0
    else:
        cc_cpu, cc_power, _ = C.CC_LEVELS[layout.cc_level]
    a.cpu_capacity, a.power_capacity = cc_cpu, cc_power

    # --- structures
    specs: list[Optional[StructureSpec]] = []
    cpu = power = 0.0
    for i, p in enumerate(layout.pins, start=1):
        spec = static.structures.get(p.type_id)
        specs.append(spec)
        if spec is None or spec.kind == C.KIND_COMMAND_CENTER:
            a.kinds.append(None)
            a.add(ERROR, "unknown_structure", f"Pin {i}: structure type {p.type_id} is not a placeable PI structure", pins=(i,))
            continue
        a.kinds.append(spec.kind)
        cpu += spec.cpu
        power += spec.power
        a.setup_isk += spec.isk_cost
        if spec.planet_type_id is not None and spec.planet_type_id != layout.planet_type_id:
            # Real exports mix planet types (e.g. an Ice launchpad on a Barren
            # template) and the game accepts it (P-19) - info only.
            pass
        if spec.kind == C.KIND_ECU:
            if not (1 <= p.heads <= C.MAX_EXTRACTOR_HEADS):
                a.add(ERROR, "heads", f"Pin {i}: an extractor needs 1-{C.MAX_EXTRACTOR_HEADS} heads", pins=(i,))
            cpu += p.heads * static.head_cpu
            power += p.heads * static.head_power
            if p.product is not None and p.product not in C.PLANET_RESOURCES.get(layout.planet_type_id, ()):
                a.add(WARNING, "resource_missing",
                      f"Pin {i}: {static.name(p.product)} does not occur on this planet type", pins=(i,))
        if spec.kind in C.FACTORY_KINDS:
            s = static.schematic_by_output.get(p.product) if p.product else None
            if s is None:
                a.add(WARNING, "no_schematic", f"Pin {i}: factory without a schematic", pins=(i,))
            elif spec.type_id not in s.pin_type_ids and not _same_kind_allowed(static, spec, s):
                a.add(ERROR, "wrong_facility",
                      f"Pin {i}: {static.name(p.product)} cannot be made in a {spec.name}", pins=(i,))
            if spec.kind == C.KIND_HIGH_TECH and not static.has_kind(C.KIND_HIGH_TECH, layout.planet_type_id):
                a.add(ERROR, "no_high_tech", "High-Tech Production Plants exist only on Barren and Temperate planets",
                      pins=(i,))
        a.storage_m3 += spec.capacity if spec.kind in C.HUB_KINDS else 0.0

    # --- spacing (after the rounding templates use)
    pins = layout.pins
    for i in range(len(pins)):
        for j in range(i + 1, len(pins)):
            ang = central_angle(_round5(pins[i].la), _round5(pins[i].lo), _round5(pins[j].la), _round5(pins[j].lo))
            if ang < C.MIN_PIN_SEPARATION_RAD - 1e-9:
                a.add(ERROR, "too_close",
                      f"Pins {i + 1} and {j + 1} are too close ({ang:.4f} rad < {C.MIN_PIN_SEPARATION_RAD}) - the game refuses the import",
                      pins=(i + 1, j + 1))

    # --- links
    adjacency: dict[tuple[int, int], int] = {}
    for k, lk in enumerate(layout.links):
        pa, pb = pins[lk.a - 1], pins[lk.b - 1]
        km = central_angle(pa.la, pa.lo, pb.la, pb.lo) * radius
        a.link_km.append(km)
        c, pw = static.link.cost(km, lk.level)
        a.link_cpu += c
        a.link_power += pw
        a.link_capacity_m3h.append(static.link.capacity_at(lk.level))
        key = (min(lk.a, lk.b), max(lk.a, lk.b))
        if key in adjacency:
            a.add(WARNING, "duplicate_link", f"Link {k + 1} duplicates link {adjacency[key] + 1}", link=k + 1)
        adjacency.setdefault(key, k)
    cpu += a.link_cpu
    power += a.link_power
    a.cpu_used, a.power_used = int(math.ceil(cpu - 1e-9)), int(math.ceil(power - 1e-9))
    if a.cpu_used > cc_cpu:
        a.add(ERROR, "cpu", f"CPU {a.cpu_used:,} tf exceeds the Command Center's {cc_cpu:,} tf")
    if a.power_used > cc_power:
        a.add(ERROR, "power", f"Power {a.power_used:,} MW exceeds the Command Center's {cc_power:,} MW")

    # --- routes
    a.link_load_m3h = [0.0] * len(layout.links)
    for r_i, r in enumerate(layout.routes, start=1):
        if len(r.path) > C.MAX_ROUTE_STRUCTURES:
            a.add(ERROR, "route_too_long",
                  f"Route {r_i} passes {len(r.path)} structures - the game builds at most {C.MAX_ROUTE_STRUCTURES} and silently drops it",
                  route=r_i)
        for x, y in zip(r.path, r.path[1:]):
            if (min(x, y), max(x, y)) not in adjacency:
                a.add(ERROR, "route_unlinked", f"Route {r_i} jumps between pins {x} and {y}, which are not linked",
                      route=r_i, pins=(x, y))
        tier = static.tier(r.commodity)
        if tier == 0:
            # jwebbdev reports such routes fail silently on import, but real
            # in-game exports (DalShooth's miner templates) contain them - so
            # a hint, not an error. Our generator never builds them.
            for x in r.path[1:-1]:
                if a.kinds[x - 1] == C.KIND_BASIC:
                    a.add(INFO, "p0_through_basic",
                          f"Route {r_i} carries P0 through a Basic facility (pin {x}); reported to fail on some imports",
                          route=r_i)
                    break

    _throughput(static, layout, a, yield_per_head)

    # --- link loads from the solved route flows
    for k, cap in enumerate(a.link_capacity_m3h):
        if a.link_load_m3h[k] > cap + 1e-6:
            need = 0
            while static.link.capacity_at(need) < a.link_load_m3h[k] and need < 10:
                need += 1
            a.add(WARNING, "link_overloaded",
                  f"Link {k + 1} carries {a.link_load_m3h[k]:,.0f} m3/h over its {cap:,.0f} m3/h - upgrade it to level {need}",
                  link=k + 1)

    denom = max(a.import_m3_per_hour, a.export_m3_per_hour)
    a.buffer_hours = (a.storage_m3 / denom) if denom > 1e-9 else None
    _classify(static, a)
    return a


def _same_kind_allowed(static: StaticData, spec: StructureSpec, s) -> bool:
    """A factory of another planet type but the same kind may run the
    schematic too (templates mix planet types)."""
    for pin in s.pin_type_ids:
        other = static.structures.get(pin)
        if other is not None and other.kind == spec.kind:
            return True
    return False


def _throughput(static: StaticData, layout: Layout, a: Analysis, yield_per_head: float) -> None:
    """Steady-state LP. Variables: runs/h per factory, flow/h per route,
    import/h per (hub, commodity) for commodities a route takes out of a hub.
    Maximise tier-weighted runs; imports cost a little so on-planet supply is
    preferred."""
    import numpy as np
    from scipy.optimize import linprog

    pins = layout.pins
    factories = [i for i, k in enumerate(a.kinds, start=1) if k in C.FACTORY_KINDS and pins[i - 1].product]
    ecus = [i for i, k in enumerate(a.kinds, start=1) if k == C.KIND_ECU and pins[i - 1].product]
    hubs = {i for i, k in enumerate(a.kinds, start=1) if k in C.HUB_KINDS}
    schem = {f: static.schematic_by_output.get(pins[f - 1].product) for f in factories}
    factories = [f for f in factories if schem[f] is not None]
    routes = list(enumerate(layout.routes, start=1))
    if not routes and not factories:
        return

    # node balance: every pin is a node; per (node, commodity) flow conservation:
    # inflow (routes in + produced/extracted/imported) = outflow (routes out + consumed + exported)
    var: list[tuple] = []
    for f in factories:
        var.append(("run", f))
    for r_i, _r in routes:
        var.append(("flow", r_i))
    import_keys = sorted({(r.path[0], r.commodity) for _ri, r in routes if r.path[0] in hubs})
    for key in import_keys:
        var.append(("import", key))
    export_keys = sorted({(r.path[-1], r.commodity) for _ri, r in routes if r.path[-1] in hubs})
    for key in export_keys:
        var.append(("export", key))
    # ECU surplus that no route takes would just pile up in the ECU - not modelled.
    idx = {v: n for n, v in enumerate(var)}
    nvar = len(var)
    if nvar == 0:
        return

    bounds = []
    for v in var:
        if v[0] == "run":
            s = schem[v[1]]
            bounds.append((0.0, s.runs_per_hour))
            a.max_runs_per_hour[v[1]] = s.runs_per_hour
        elif v[0] == "flow":
            r = layout.routes[v[1] - 1]
            src_kind = a.kinds[r.path[0] - 1]
            if src_kind == C.KIND_ECU:
                cap = pins[r.path[0] - 1].heads * yield_per_head
            elif src_kind in C.FACTORY_KINDS and schem.get(r.path[0]):
                cap = None  # bounded by production
            else:
                dst = r.path[-1]
                s = schem.get(dst)
                cap = r.quantity * s.runs_per_hour if s else None
            bounds.append((0.0, cap))
        else:
            bounds.append((0.0, None))

    rows_eq, rhs_eq = [], []
    rows_ub, rhs_ub = [], []
    by_node_commodity: dict[tuple[int, int], dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for r_i, r in routes:
        by_node_commodity[(r.path[0], r.commodity)][idx[("flow", r_i)]] -= 1.0   # leaves source
        by_node_commodity[(r.path[-1], r.commodity)][idx[("flow", r_i)]] += 1.0  # arrives at destination
    for f in factories:
        s = schem[f]
        by_node_commodity[(f, s.output_type_id)][idx[("run", f)]] += s.output_qty
        for t, q in s.inputs:
            by_node_commodity[(f, t)][idx[("run", f)]] -= q
    for key in import_keys:
        by_node_commodity[key][idx[("import", key)]] += 1.0
    for key in export_keys:
        by_node_commodity[key][idx[("export", key)]] -= 1.0
    ecu_supply: dict[tuple[int, int], float] = {}
    for e in ecus:
        ecu_supply[(e, pins[e - 1].product)] = pins[e - 1].heads * yield_per_head
    for (node, t), coeffs in by_node_commodity.items():
        row = np.zeros(nvar)
        for j, c in coeffs.items():
            row[j] = c
        kind = a.kinds[node - 1]
        if kind in C.FACTORY_KINDS:
            # factory: inputs consumed = inflow; output produced = outflow
            rows_eq.append(row)
            rhs_eq.append(0.0)
        elif kind == C.KIND_ECU:
            # extracted units leave over routes: outflow <= supply
            rows_ub.append(-row)
            rhs_ub.append(ecu_supply.get((node, t), 0.0))
        else:
            # hub: balance must hold (imports/exports close it)
            rows_eq.append(row)
            rhs_eq.append(0.0)

    c = np.zeros(nvar)
    for v, j in idx.items():
        if v[0] == "run":
            tier = static.tier(pins[v[1] - 1].product) or 1
            c[j] = -(10.0 ** tier)
        elif v[0] == "import":
            c[j] = 1e-3
        elif v[0] == "export":
            c[j] = -1e-6
    res = linprog(c, A_ub=np.array(rows_ub) if rows_ub else None, b_ub=np.array(rhs_ub) if rhs_ub else None,
                  A_eq=np.array(rows_eq) if rows_eq else None, b_eq=np.array(rhs_eq) if rhs_eq else None,
                  bounds=bounds, method="highs")
    if not res.success:
        a.add(WARNING, "throughput_unsolved", f"Throughput could not be computed ({res.message})")
        return
    x = res.x
    for f in factories:
        runs = float(x[idx[("run", f)]])
        a.runs_per_hour[f] = runs
        s = schem[f]
        a.produced[s.output_type_id] = a.produced.get(s.output_type_id, 0.0) + runs * s.output_qty
        for t, q in s.inputs:
            a.consumed[t] = a.consumed.get(t, 0.0) + runs * q
        if runs < 1e-9:
            a.add(WARNING, "factory_idle", f"Pin {f}: {static.name(s.output_type_id)} factory never runs "
                  "(an input or the output has no route)", pins=(f,))
    for (node, t), supply in ecu_supply.items():
        a.extracted[t] = a.extracted.get(t, 0.0) + supply
    for key in import_keys:
        v = float(x[idx[("import", key)]])
        if v > 1e-9:
            a.imports[key[1]] = a.imports.get(key[1], 0.0) + v
    for key in export_keys:
        v = float(x[idx[("export", key)]])
        if v > 1e-9:
            a.exports[key[1]] = a.exports.get(key[1], 0.0) + v

    def vol(t):
        cm = static.commodities.get(t)
        return cm.volume if cm else 0.0

    a.import_m3_per_hour = sum(q * vol(t) for t, q in a.imports.items())
    a.export_m3_per_hour = sum(q * vol(t) for t, q in a.exports.items())
    link_index = {(min(lk.a, lk.b), max(lk.a, lk.b)): k for k, lk in enumerate(layout.links)}
    for r_i, r in routes:
        m3 = float(x[idx[("flow", r_i)]]) * vol(r.commodity)
        for p, q in zip(r.path, r.path[1:]):
            k = link_index.get((min(p, q), max(p, q)))
            if k is not None:
                a.link_load_m3h[k] += m3


def _classify(static: StaticData, a: Analysis) -> None:
    """Product = highest-tier commodity produced; chain from the lowest raw
    tier (extracted -> 0, else the lowest imported tier)."""
    from ..model import CHAINS

    made = [t for t, q in a.produced.items() if q > 1e-9]
    if not made:
        return
    product = max(made, key=lambda t: (static.tier(t) or 0, a.produced[t]))
    a.product_type_id = product
    target = static.tier(product) or 0
    # Source tier from the number of stages made on the planet, not from the
    # lowest import: a P3->P4 Nano-Factory colony also imports a P1.
    stages = {static.tier(t) or 0 for t in made}
    source = 0 if a.extracted else target - len(stages)
    for name, (s, t) in CHAINS.items():
        if (s, t) == (source, target):
            a.chain = name

"""Template generator (docs/PI_PLAN.md 6A, docs/PI_TECHNICAL_DESIGN.md 4).

Pipeline: design -> pins -> placement -> link tree -> ordered routes ->
validate -> link levels -> budget feedback. Built for this app; the game
rules come from Eve-PI's in-game measurements, nothing is ported.

Topology:
- launchpad #1 sits in the centre cell ("core hub"); further launchpads and
  storage facilities take the first ring, so each heads a subtree;
- factories fill the rings outward (higher tiers nearer the core), ECUs go
  last, on the outside;
- every pin links to its nearest already-placed pin one ring further in, so
  the tree depth equals the ring index and a route from a pin to any of its
  ancestors passes ring + 1 structures. Rings are capped at 6, so no route
  can exceed the game's 7-structure limit (P-42) - the validator still checks;
- each pin's "home hub" is its nearest hub ancestor. Raw imports and
  sellable outputs use the home hub (spreads goods over every pad/storage
  so their capacity is actually used); goods made and used on the planet
  (P0 from extractors, intermediates) go through the core hub, which every
  pin reaches as an ancestor;
- routes are created input-first per factory: EVE drains a factory's input
  routes in creation order, so the home hub comes first.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable, Optional

from .. import constants as C
from ..engine import made_in
from ..model import CHAINS, Design, StaticData, factory_kind_for_tier
from . import validate
from .geometry import EQUATOR, central_angle
from .template_io import Layout, Link, Pin, Route, safe_comment

SPACING = C.MIN_PIN_SEPARATION_RAD * 1.05
MAX_RING = 6
MAX_FEEDBACK_ITERATIONS = 10


class GenerateError(ValueError):
    """The design cannot be laid out (with a user-facing reason)."""


@dataclass(frozen=True)
class Cell:
    q: int
    r: int

    @property
    def ring(self) -> int:
        return max(abs(self.q), abs(self.r), abs(-self.q - self.r))


def hex_cells(max_ring: int = MAX_RING) -> list[Cell]:
    """Axial hex cells sorted by ring, then by angle - a stable fill order."""
    cells = []
    for q in range(-max_ring, max_ring + 1):
        for r in range(-max_ring, max_ring + 1):
            c = Cell(q, r)
            if c.ring <= max_ring:
                cells.append(c)

    def key(c: Cell):
        x, y = cell_xy(c)
        return (c.ring, round(math.atan2(y, x), 9))

    return sorted(cells, key=key)


def cell_xy(c: Cell) -> tuple[float, float]:
    """Flat offset in units of SPACING (x east, y towards larger La)."""
    return c.q + c.r / 2.0, c.r * math.sqrt(3) / 2.0


def cell_position(c: Cell, spacing: float = SPACING) -> tuple[float, float]:
    """(La, Lo) on the sphere around the equator. Rows are placed by polar
    offset; along a row the longitude step is scaled by 1/sin(La) so the
    great-circle spacing stays `spacing` (P-41). Rounded to 5 decimals like
    real templates - the validator re-checks spacing after rounding (P-25)."""
    x, y = cell_xy(c)
    la = EQUATOR + y * spacing
    lo = x * spacing / math.sin(la)
    return round(la, 5), round(lo, 5)


# A provider returns the ordered cells pins are placed on (shapes, 5c).
CellProvider = Callable[[int], list[Cell]]


def standard_cells(n: int) -> list[Cell]:
    cells = hex_cells()
    if n > len(cells):
        raise GenerateError(f"{n} structures do not fit within {MAX_RING} rings")
    return cells[:n]


@dataclass
class _Spec:
    role: str                  # "hub" | "factory" | "ecu"
    kind: str
    product: Optional[int] = None
    heads: int = 0


def _pins_for(static: StaticData, design: Design) -> list[_Spec]:
    hubs = [_Spec("hub", C.KIND_LAUNCHPAD)]
    hubs += [_Spec("hub", C.KIND_LAUNCHPAD) for _ in range(design.launchpads - 1)]
    hubs += [_Spec("hub", C.KIND_STORAGE) for _ in range(design.storages)]
    factories = []
    for t, n in sorted(design.factories, key=lambda tn: (-(static.tier(tn[0]) or 0), tn[0])):
        kind = factory_kind_for_tier(static.tier(t) or 0)
        factories += [_Spec("factory", kind, t) for _ in range(n)]
    ecus = [_Spec("ecu", C.KIND_ECU, p0, heads) for p0, heads in design.ecus]
    return hubs + factories + ecus


def build_layout(static: StaticData, design: Design, planet_type_id: int, radius_km: float,
                 yield_per_head: float, comment: str = "",
                 cells_fn: CellProvider = standard_cells) -> Layout:
    """Pins, links and routes for `design` - not yet validated."""
    specs = _pins_for(static, design)
    if not specs:
        raise GenerateError("Empty design")
    cells = cells_fn(len(specs))
    positions = [cell_position(c) for c in cells]

    # Hubs must take the core and (as far as there are hubs) the first ring.
    type_ids = []
    for s in specs:
        spec = static.structure(s.kind, planet_type_id)
        if spec is None:
            raise GenerateError(f"No {s.kind.replace('_', ' ')} structure exists on this planet type")
        type_ids.append(spec.type_id)

    # --- link tree: each pin to the nearest placed pin one ring further in
    n = len(specs)
    rings = [c.ring for c in cells]
    parent: list[Optional[int]] = [None] * n
    children = [0] * n
    # Basic facilities and extractors are always leaves attached straight to a
    # hub: P0 then only ever passes hubs (our generator never routes P0
    # through a Basic facility, see the validator's p0_through_basic). Other
    # pins attach to the nearest inner hub or Advanced/High-Tech factory; a
    # link may span more than one cell when no closer parent qualifies.
    def can_parent(j: int) -> bool:
        return specs[j].role == "hub" or specs[j].kind in (C.KIND_ADVANCED, C.KIND_HIGH_TECH)

    def needs_hub(i: int) -> bool:
        return specs[i].kind in (C.KIND_BASIC, C.KIND_ECU)

    for i in range(1, n):
        best_j, best_key = None, None
        for strict in (True, False):
            for j in range(i):
                if not can_parent(j) or (needs_hub(i) and specs[j].role != "hub"):
                    continue
                if strict and rings[j] >= rings[i]:
                    continue
                d = central_angle(*positions[i], *positions[j])
                key = (round(d, 7), children[j], j)
                if best_key is None or key < best_key:
                    best_j, best_key = j, key
            if best_j is not None:
                break
        if best_j is None:
            raise GenerateError("internal: no parent for a structure")
        parent[i] = best_j
        children[best_j] += 1
    links = [Link(a=i + 1, b=parent[i] + 1, level=0) for i in range(1, n)]

    def ancestors(i: int) -> list[int]:
        path = [i]
        while parent[path[-1]] is not None:
            path.append(parent[path[-1]])
        return path

    hub_idx = {i for i, s in enumerate(specs) if s.role == "hub"}

    def path_to(i: int, target: int) -> list[int]:
        up = ancestors(i)
        if target not in up:
            raise GenerateError("internal: route target is not an ancestor")
        return up[: up.index(target) + 1]

    def home_hub(i: int) -> int:
        for a in ancestors(i)[1:] if specs[i].role != "hub" else ancestors(i):
            if a in hub_idx:
                return a
        return 0

    core = 0
    made = set(made_in(static, design))
    routes: list[Route] = []
    for i, s in enumerate(specs):
        if s.role != "factory":
            continue
        sch = static.schematic_by_output[s.product]
        home = home_hub(i)
        for t, q in sch.inputs:
            internal = t in made or (static.tier(t) == 0 and design.ecus)
            src = core if internal else home
            p = path_to(i, src)
            routes.append(Route(tuple(x + 1 for x in reversed(p)), float(q), t))
        out = sch.output_type_id
        dst = core if (out in made and out != design.product_type_id) else home
        routes.append(Route(tuple(x + 1 for x in path_to(i, dst)), float(sch.output_qty), out))
    for i, s in enumerate(specs):
        if s.role == "ecu":
            routes.append(Route(tuple(x + 1 for x in path_to(i, core)),
                                float(max(1, int(s.heads * yield_per_head))), s.product))

    pins = tuple(Pin(type_id=type_ids[i], la=positions[i][0], lo=positions[i][1],
                     product=specs[i].product, heads=specs[i].heads)
                 for i in range(n))
    return Layout(
        cc_level=design.cc_level,
        comment=safe_comment(comment or f"{design.chain} {static.name(design.product_type_id)}"),
        diameter_km=float(round(radius_km * 2.0, 1)),
        planet_type_id=planet_type_id,
        pins=pins, links=tuple(links), routes=tuple(routes),
    )


def _set_link_levels(static: StaticData, layout: Layout, analysis: validate.Analysis) -> Layout:
    links = list(layout.links)
    changed = False
    for k, load in enumerate(analysis.link_load_m3h):
        lv = 0
        while static.link.capacity_at(lv) < load - 1e-6 and lv < 5:
            lv += 1
        if lv != links[k].level:
            links[k] = replace(links[k], level=lv)
            changed = True
    return replace(layout, links=tuple(links)) if changed else layout


@dataclass
class GenerateResult:
    layout: Layout
    analysis: validate.Analysis
    design: Design
    iterations: int
    notes: list[str]


def _shrink(static: StaticData, design: Design) -> Optional[Design]:
    """One factory fewer in the largest stage (top stage on ties), or one
    head fewer on the biggest extractor - the budget feedback step (P-43)."""
    facs = dict(design.factories)
    if facs and sum(facs.values()) > 1:
        t = max(facs, key=lambda x: (facs[x], static.tier(x) or 0))
        if facs[t] > 1:
            facs[t] -= 1
            return replace(design, factories=tuple(sorted(facs.items())))
    if design.storages > 0:
        return replace(design, storages=design.storages - 1)
    if design.launchpads > 1:
        return replace(design, launchpads=design.launchpads - 1)
    if design.ecus:
        ecus = list(design.ecus)
        k = max(range(len(ecus)), key=lambda i: ecus[i][1])
        if ecus[k][1] > 1:
            ecus[k] = (ecus[k][0], ecus[k][1] - 1)
            return replace(design, ecus=tuple(ecus))
    return None


def _attempt(static, design, planet_type_id, radius_km, yield_per_head, comment, cells_fn):
    layout = build_layout(static, design, planet_type_id, radius_km, yield_per_head, comment, cells_fn)
    analysis = validate.analyse(static, layout, radius_km, yield_per_head)
    for _ in range(3):
        leveled = _set_link_levels(static, layout, analysis)
        if leveled is layout:
            break
        layout = leveled
        analysis = validate.analyse(static, layout, radius_km, yield_per_head)
    errors = [f for f in analysis.findings if f.severity == validate.ERROR]
    return layout, analysis, errors


def _grow(static: StaticData, design: Design, target: Design) -> Optional[Design]:
    """One factory back towards `target` (largest shortfall first)."""
    have, want = dict(design.factories), dict(target.factories)
    gaps = {t: want[t] - have.get(t, 0) for t in want if want[t] > have.get(t, 0)}
    if not gaps:
        return None
    t = max(gaps, key=lambda x: (gaps[x], static.tier(x) or 0))
    have[t] = have.get(t, 0) + 1
    return replace(design, factories=tuple(sorted(have.items())))


def generate(static: StaticData, design: Design, planet_type_id: int, radius_km: float,
             yield_per_head: float, comment: str = "", cells_fn: CellProvider = standard_cells,
             shrink_to_fit: bool = True) -> GenerateResult:
    """Lay out `design`, set link levels for the real loads, and - if exact
    link costs push it over the Command Center budget - drop structures until
    it fits, then add factories back one at a time while it still fits
    (bounded loops, P-43). Never returns a layout the validator rejects."""
    notes: list[str] = []
    current = design
    iterations = 0
    # shrink: proportional to the overshoot, so a far-too-big design converges fast
    while True:
        iterations += 1
        layout, analysis, errors = _attempt(static, current, planet_type_id, radius_km, yield_per_head, comment, cells_fn)
        if not errors:
            break
        budget_only = all(f.code in ("cpu", "power") for f in errors)
        if not (budget_only and shrink_to_fit):
            raise GenerateError("; ".join(f.message for f in errors[:3]))
        if iterations > MAX_FEEDBACK_ITERATIONS * 4:
            raise GenerateError("Could not fit the colony within the Command Center budget")
        over = max(analysis.cpu_used / max(analysis.cpu_capacity, 1), analysis.power_used / max(analysis.power_capacity, 1)) - 1.0
        steps = max(1, math.ceil(over * max(current.total_factories, 1)))
        for _ in range(steps):
            smaller = _shrink(static, current)
            if smaller is None:
                break
            current = smaller
        else:
            continue
        if smaller is None:
            raise GenerateError("Even the smallest version of this colony exceeds the Command Center budget")
    # grow back towards the planned design while it still fits
    for _ in range(MAX_FEEDBACK_ITERATIONS * 4):
        bigger = _grow(static, current, design)
        if bigger is None:
            break
        b_layout, b_analysis, b_errors = _attempt(static, bigger, planet_type_id, radius_km, yield_per_head, comment, cells_fn)
        iterations += 1
        if b_errors:
            break
        current, layout, analysis = bigger, b_layout, b_analysis
    if current != design:
        notes.append("Exact link costs did not fit the planned design; structures were removed until it fit.")
    return GenerateResult(layout, analysis, current, iterations, notes)

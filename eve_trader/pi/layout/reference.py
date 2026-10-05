"""Reference layouts: start from a proven community template instead of an
empty hex grid.

`reference_templates.json` holds public templates shared on planetsin.space
(comments and authors removed, only layouts the validator accepts, duplicates
merged). For a design, `from_references`:

1. takes the references of the same chain whose Command Center level fits,
   closest structure counts first;
2. maps each one's recipe tree onto the design's products (`map_products`:
   same tiers, same made / extracted / imported status, same shape of inputs)
   and rewrites only products, recipes, route quantities and the planet's
   structure ids - pins, links and route paths stay (`adapt`);
3. re-validates on the real planet radius, with the colony's own products
   and extracted P0 barred from being hauled in;
4. splits the factories between the stages in the design's proportions
   for this recipe (`rebalance`), trims factories the colony cannot feed at
   our yields (`prune`), then
   grows the best few while CPU and power allow (`grow`): copies of an
   existing factory or extractor next to a hub it already uses, with its
   routes, or one more extractor head - nothing that stays is moved;
5. returns the one with the highest effective output (`effective_output`),
   or None when no reference fits. The caller compares it with its own
   generator and keeps the better one.

The structure counts come from the reference, not from the design: the result
carries its own Design so the caller reports what the layout really builds.
"""
from __future__ import annotations

import itertools
import json
import math
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from .. import constants as C
from ..engine import made_in
from ..model import Design, StaticData
from . import validate
from .geometry import central_angle, offset
from .template_io import Layout, Link, Pin, Route, parse, safe_comment

_PATH = Path(__file__).with_name("reference_templates.json")
# Mapping is cheap; analysing a candidate (throughput LP) is not, and growing
# one analyses every possible step. Only the best-ranked references go on.
MAX_ANALYSED = 12
MAX_GROWN = 3
MAX_GROW_STEPS = 30

_lock = threading.Lock()
_cache: Optional[list["Reference"]] = None


@dataclass(frozen=True)
class Reference:
    chain: str
    cc_level: int
    upvotes: int
    layout: Layout


def load() -> list[Reference]:
    global _cache
    with _lock:
        if _cache is None:
            doc = json.loads(_PATH.read_text(encoding="utf-8"))
            _cache = [Reference(r["chain"], int(r["cc_level"]), int(r["upvotes"]), parse(r["template"]))
                      for r in doc["templates"]]
        return _cache


# ------------------------------------------------------------ recipe mapping
def _kind(static: StaticData, type_id: int) -> Optional[str]:
    spec = static.structures.get(type_id)
    return spec.kind if spec else None


def _layout_made(static: StaticData, layout: Layout) -> set[int]:
    return {p.product for p in layout.pins
            if p.product is not None and _kind(static, p.type_id) in C.FACTORY_KINDS}


def _layout_extracted(static: StaticData, layout: Layout) -> set[int]:
    return {p.product for p in layout.pins if p.product is not None and _kind(static, p.type_id) == C.KIND_ECU}


def map_products(static: StaticData, ref_top: int, ref_made: set[int], ref_local_p0: set[int],
                 new_top: int, new_made: set[int], new_local_p0: set[int]) -> Optional[dict[int, int]]:
    """A one-to-one commodity mapping from the reference's recipe tree onto
    the design's, or None. Each made product maps to a made product of the
    same tier whose inputs pair up with the same tiers, the same made /
    extracted / imported status and the same shape below; ties are tried in
    every order (at most 3 inputs per recipe)."""

    def status(t: int, made: set[int], local: set[int]) -> str:
        return "made" if t in made else ("extracted" if t in local else "imported")

    def inputs(t: int) -> list[int]:
        s = static.schematic_by_output.get(t)
        return [i for i, _q in s.inputs] if s else []

    def assign(r: int, n: int, m: dict[int, int], back: dict[int, int]) -> Optional[tuple[dict, dict]]:
        if r in m:
            return (m, back) if m[r] == n else None
        if n in back or static.tier(r) != static.tier(n):
            return None
        if status(r, ref_made, ref_local_p0) != status(n, new_made, new_local_p0):
            return None
        m, back = {**m, r: n}, {**back, n: r}
        if r not in ref_made:
            return m, back
        r_in, n_in = inputs(r), inputs(n)
        if len(r_in) != len(n_in):
            return None
        for perm in itertools.permutations(n_in):
            state: Optional[tuple[dict, dict]] = (m, back)
            for ri, ni in zip(r_in, perm):
                state = assign(ri, ni, *state)
                if state is None:
                    break
            if state is not None:
                return state
        return None

    result = assign(ref_top, new_top, {}, {})
    if result is None:
        return None
    m = result[0]
    # Everything the reference makes or extracts must be part of the tree.
    if (ref_made | ref_local_p0) - set(m):
        return None
    return m


# ------------------------------------------------------------ adaptation
def adapt(static: StaticData, ref: Reference, design: Design, planet_type_id: int, radius_km: float,
          yield_per_head: float, comment: str = "") -> Optional[Layout]:
    """The reference rebuilt for `design`'s product on `planet_type_id`, not
    yet validated - or None when its recipe tree does not fit."""
    lay = ref.layout
    if ref.chain != design.chain or ref.cc_level > design.cc_level:
        return None
    ref_made = _layout_made(static, lay)
    consumed = {i for o in ref_made if o in static.schematic_by_output
                for i, _q in static.schematic_by_output[o].inputs}
    ref_tops = [t for t in ref_made if t not in consumed]
    if len(ref_tops) != 1:
        return None
    new_made = set(made_in(static, design))
    m = map_products(static, ref_tops[0], ref_made, _layout_extracted(static, lay),
                     design.product_type_id, new_made, {p for p, _h in design.ecus})
    if m is None:
        return None
    resources = C.PLANET_RESOURCES.get(planet_type_id, ())
    pins = []
    for p in lay.pins:
        spec = static.structures.get(p.type_id)
        target = static.structure(spec.kind, planet_type_id) if spec else None
        if target is None:
            return None
        product = m.get(p.product) if p.product is not None else None
        if p.product is not None and product is None:
            return None
        if spec.kind == C.KIND_ECU and product not in resources:
            return None
        pins.append(replace(p, type_id=target.type_id, product=product))
    routes = []
    for r in lay.routes:
        commodity = m.get(r.commodity)
        if commodity is None:
            return None
        routes.append(replace(r, commodity=commodity,
                              quantity=_route_quantity(static, pins, r.path, commodity, r.quantity, yield_per_head)))
    return Layout(
        cc_level=design.cc_level,
        comment=safe_comment(comment or f"{design.chain} {static.name(design.product_type_id)}"),
        diameter_km=float(round(radius_km * 2.0, 1)),
        planet_type_id=planet_type_id,
        pins=tuple(pins), links=lay.links, routes=tuple(routes),
    )


def _route_quantity(static: StaticData, pins: list[Pin], path: tuple[int, ...], commodity: int,
                    old_qty: float, yield_per_head: float) -> float:
    """Per-cycle quantity for the new recipe: what the destination factory
    consumes, what the source factory makes, or what the source extractor
    yields; hub-to-hub routes keep theirs."""
    src, dst = pins[path[0] - 1], pins[path[-1] - 1]
    if _kind(static, dst.type_id) in C.FACTORY_KINDS:
        s = static.schematic_by_output.get(dst.product)
        q = dict(s.inputs).get(commodity) if s else None
        if q is not None:
            return float(q)
    if _kind(static, src.type_id) in C.FACTORY_KINDS and src.product == commodity:
        return float(static.schematic_by_output[commodity].output_qty)
    if _kind(static, src.type_id) == C.KIND_ECU:
        return float(max(1, int(src.heads * yield_per_head)))
    return float(old_qty)


def design_of(static: StaticData, layout: Layout, chain: str, product_type_id: int) -> Design:
    """The structure counts a layout really builds."""
    factories: dict[int, int] = {}
    ecus, launchpads, storages = [], 0, 0
    for p in layout.pins:
        kind = _kind(static, p.type_id)
        if kind in C.FACTORY_KINDS:
            factories[p.product] = factories.get(p.product, 0) + 1
        elif kind == C.KIND_ECU:
            ecus.append((p.product, p.heads))
        elif kind == C.KIND_LAUNCHPAD:
            launchpads += 1
        elif kind == C.KIND_STORAGE:
            storages += 1
    return Design(chain=chain, product_type_id=product_type_id, planet_type_id=layout.planet_type_id,
                  cc_level=layout.cc_level, factories=tuple(sorted(factories.items())),
                  ecus=tuple(sorted(ecus)), launchpads=launchpads, storages=storages)


@dataclass
class ReferenceResult:
    layout: Layout
    analysis: validate.Analysis
    design: Design
    reference: Reference
    effective_output: float


def effective_output(analysis: validate.Analysis, product_type_id: int, interval_hours: float) -> float:
    """Product units/h when the colony is visited once per collection
    interval - the engine's own measure (raw output x min(1, storage hours /
    interval)). A layout with one launchpad for many factories runs dry
    between visits; community layouts carry storage for days."""
    raw = float(analysis.exports.get(product_type_id, 0.0))
    if analysis.buffer_hours is None:
        return raw
    return raw * min(1.0, analysis.buffer_hours / max(float(interval_hours), 1e-9))


def self_supplied(static: StaticData, design: Design) -> frozenset:
    """What the colony makes or extracts itself - never hauled in."""
    return frozenset(made_in(static, design)) | frozenset(p for p, _h in design.ecus)


def settle(static: StaticData, layout: Layout, radius_km: float, yield_per_head: float,
           local: frozenset = frozenset()) -> tuple[Layout, validate.Analysis]:
    """Link levels for the real loads, then the final analysis."""
    from .generate import _set_link_levels

    analysis = validate.analyse(static, layout, radius_km, yield_per_head, local)
    for _ in range(3):
        leveled = _set_link_levels(static, layout, analysis)
        if leveled is layout:
            break
        layout = leveled
        analysis = validate.analyse(static, layout, radius_km, yield_per_head, local)
    return layout, analysis


# ------------------------------------------------------------ growing
def _neighbours(layout: Layout) -> dict[int, list[int]]:
    nb: dict[int, list[int]] = {i: [] for i in range(1, len(layout.pins) + 1)}
    for lk in layout.links:
        nb[lk.a].append(lk.b)
        nb[lk.b].append(lk.a)
    return nb


def _shortest_path(nb: dict[int, list[int]], src: int, dst: int) -> Optional[list[int]]:
    prev: dict[int, Optional[int]] = {src: None}
    queue = [src]
    for node in queue:
        if node == dst:
            path = [dst]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return path[::-1]
        for n in sorted(nb[node]):
            if n not in prev:
                prev[n] = node
                queue.append(n)
    return None


def _free_spot(layout: Layout, hub: int) -> Optional[tuple[float, float]]:
    """The free position closest to `hub` that keeps the generator's spacing
    (with its margin) to every pin, after the templates' 5-decimal rounding."""
    from .generate import SPACING

    h = layout.pins[hub - 1]
    for ring in range(1, 5):
        steps = 6 * ring
        for k in range(steps):
            ang = 2 * math.pi * k / steps
            la, lo = offset(h.la, h.lo, ring * SPACING * math.sin(ang), ring * SPACING * math.cos(ang))
            la, lo = round(la, 5), round(lo, 5)
            if all(central_angle(la, lo, round(p.la, 5), round(p.lo, 5)) >= SPACING for p in layout.pins):
                return la, lo
    return None


def _add_factory(static: StaticData, layout: Layout, sibling: int) -> Optional[Layout]:
    """One more factory (or extractor) like pin `sibling`: linked to a hub the sibling is
    linked to, with copies of the sibling's routes (same source or
    destination, the path re-run over the links)."""
    nb = _neighbours(layout)
    hubs = [n for n in nb[sibling] if _kind(static, layout.pins[n - 1].type_id) in C.HUB_KINDS]
    for hub in hubs:
        spot = _free_spot(layout, hub)
        if spot is None:
            continue
        new = len(layout.pins) + 1
        nb2 = {k: list(v) for k, v in nb.items()}
        nb2[new] = [hub]
        nb2[hub].append(new)
        routes = list(layout.routes)
        ok = True
        for r in layout.routes:
            if r.path[-1] == sibling:
                path = _shortest_path(nb2, r.path[0], new)
            elif r.path[0] == sibling:
                path = _shortest_path(nb2, new, r.path[-1])
            else:
                continue
            if path is None or len(path) > C.MAX_ROUTE_STRUCTURES:
                ok = False
                break
            routes.append(Route(tuple(path), r.quantity, r.commodity))
        if not ok:
            continue
        pins = layout.pins + (replace(layout.pins[sibling - 1], la=spot[0], lo=spot[1]),)
        return replace(layout, pins=pins, links=layout.links + (Link(a=new, b=hub, level=0),),
                       routes=tuple(routes))
    return None


def _switch(static: StaticData, layout: Layout, pin: int, model: int) -> Optional[Layout]:
    """Factory `pin` makes what factory `model` makes instead (same kind of
    facility): it keeps its place and links, its routes are replaced by
    copies of the model's, re-run over the links from where it stands."""
    p, m = layout.pins[pin - 1], layout.pins[model - 1]
    if p.product == m.product or _kind(static, p.type_id) != _kind(static, m.type_id):
        return None
    # Routes that only pass through the factory stay as they are.
    nb = _neighbours(layout)
    routes = [r for r in layout.routes if pin not in (r.path[0], r.path[-1])]
    for r in layout.routes:
        if r.path[-1] == model:
            path = _shortest_path(nb, r.path[0], pin)
        elif r.path[0] == model:
            path = _shortest_path(nb, pin, r.path[-1])
        else:
            continue
        if path is None or len(path) > C.MAX_ROUTE_STRUCTURES:
            return None
        routes.append(Route(tuple(path), r.quantity, r.commodity))
    pins = tuple(replace(q, product=m.product) if i == pin else q for i, q in enumerate(layout.pins, start=1))
    return replace(layout, pins=pins, routes=tuple(routes))


def _add_head(layout: Layout, ecu: int, yield_per_head: float) -> Optional[Layout]:
    p = layout.pins[ecu - 1]
    if p.heads >= C.MAX_EXTRACTOR_HEADS:
        return None
    heads = p.heads + 1
    pins = tuple(replace(q, heads=heads) if i == ecu else q for i, q in enumerate(layout.pins, start=1))
    routes = tuple(replace(r, quantity=float(max(1, int(heads * yield_per_head)))) if r.path[0] == ecu else r
                   for r in layout.routes)
    return replace(layout, pins=pins, routes=routes)


def _score(static: StaticData, analysis: validate.Analysis, product: int, interval_hours: float) -> tuple:
    """Effective output first; then everything produced (higher tiers worth
    more) and extracted, so a step that feeds a later one still counts."""
    produced = sum(q * 10.0 ** (static.tier(t) or 0) for t, q in analysis.produced.items())
    return (round(effective_output(analysis, product, interval_hours), 6), round(produced, 3),
            round(sum(analysis.extracted.values()), 3))


def _shares(total: int, weights: dict[int, int]) -> dict[int, int]:
    """`total` split in proportion to `weights` (largest remainder, at least
    one each while there is enough to go round)."""
    wsum = sum(weights.values())
    if wsum <= 0 or total <= 0:
        return {t: 0 for t in weights}
    raw = {t: total * w / wsum for t, w in weights.items()}
    out = {t: int(math.floor(v)) for t, v in raw.items()}
    for t in sorted(raw, key=lambda t: (-(raw[t] - out[t]), t))[: total - sum(out.values())]:
        out[t] += 1
    if total >= len(out):
        for t in out:
            while out[t] == 0:
                donor = max(out, key=lambda x: (out[x], -x))
                out[donor] -= 1
                out[t] += 1
    return out


def rebalance(static: StaticData, layout: Layout, design: Design) -> Layout:
    """Split each facility kind's factories between its products in the
    design's proportions - the engine's ratio for *this* recipe, which can
    differ from the one the reference was built for. Factories switch
    product in place (`_switch`); nothing moves."""
    for kind in C.FACTORY_KINDS:
        pins_of: dict[int, list[int]] = {}
        for i, p in enumerate(layout.pins, start=1):
            if _kind(static, p.type_id) == kind:
                pins_of.setdefault(p.product, []).append(i)
        weights = {t: design.factory_count(t) for t in pins_of}
        if len(pins_of) < 2 or not any(weights.values()):
            continue
        want = _shares(sum(len(v) for v in pins_of.values()), weights)
        for _ in range(len(layout.pins)):
            over = [t for t in pins_of if len(pins_of[t]) > want[t]]
            under = [t for t in pins_of if len(pins_of[t]) < want[t]]
            if not over or not under:
                break
            x, y = over[0], under[0]
            switched = None
            for pin in reversed(pins_of[x]):
                switched = _switch(static, layout, pin, pins_of[y][0])
                if switched is not None:
                    pins_of[x].remove(pin)
                    pins_of[y].append(pin)
                    break
            if switched is None:
                break
            layout = switched
    return layout


def _ecu_quantities(static: StaticData, layout: Layout, yield_per_head: float) -> Layout:
    """Extractor routes carry heads x yield per cycle (the editor's reconnect
    uses a flat figure)."""
    routes = tuple(replace(r, quantity=float(max(1, int(layout.pins[r.path[0] - 1].heads * yield_per_head))))
                   if _kind(static, layout.pins[r.path[0] - 1].type_id) == C.KIND_ECU else r
                   for r in layout.routes)
    return replace(layout, routes=routes)


def _without_hub(static: StaticData, layout: Layout, hub: int, yield_per_head: float) -> Optional[Layout]:
    """`hub` removed; what hung off it is linked to the nearest remaining
    hub (or factory) and re-routed - the editor's remove-and-reconnect."""
    from .edit import EditError, apply

    try:
        return _ecu_quantities(static, apply(static, layout, {"op": "remove", "pin": hub}), yield_per_head)
    except EditError:
        return None


def prune(static: StaticData, layout: Layout, analysis: validate.Analysis, product: int, radius_km: float,
          yield_per_head: float, interval_hours: float, local: frozenset) -> tuple[Layout, validate.Analysis]:
    """Drop what the colony does not need at our yields and collection
    interval, one structure at a time, as long as the effective output stays:
    leaf factories no route passes through (a reference built for a richer
    planet carries Basic facilities its extractor cannot feed, and they still
    cost power), and extra launchpads or storage facilities (a reference
    built for weekly visits buffers more than a shorter interval needs) -
    their budget can then go into factories (`grow`)."""
    from .edit import _remove

    out = effective_output(analysis, product, interval_hours)
    while True:
        nb = _neighbours(layout)
        through = {x for r in layout.routes for x in r.path[1:-1]}
        tries = []
        seen: set[int] = set()
        for i in range(len(layout.pins), 0, -1):
            p = layout.pins[i - 1]
            if (_kind(static, p.type_id) not in C.FACTORY_KINDS or p.product in seen
                    or len(nb[i]) != 1 or i in through):
                continue
            seen.add(p.product)
            tries.append(lambda i=i, lay=layout: _remove(lay, i))
        hubs = [i for i, p in enumerate(layout.pins, start=1) if _kind(static, p.type_id) in C.HUB_KINDS]
        if len(hubs) > 1:
            core = max(hubs, key=lambda i: (len(nb[i]), -i))
            for i in sorted((h for h in hubs if h != core), key=lambda i: (len(nb[i]), i)):
                tries.append(lambda i=i, lay=layout: _without_hub(static, lay, i, yield_per_head))
        done = False
        for attempt in tries:
            cand = attempt()
            if cand is None:
                continue
            cand, cand_analysis = settle(static, cand, radius_km, yield_per_head, local)
            if cand_analysis.ok and effective_output(cand_analysis, product, interval_hours) >= out - 1e-9:
                layout, analysis, done = cand, cand_analysis, True
                break
        if not done:
            return layout, analysis


def grow(static: StaticData, layout: Layout, analysis: validate.Analysis, product: int, radius_km: float,
         yield_per_head: float, interval_hours: float, local: frozenset,
         max_steps: int = MAX_GROW_STEPS) -> tuple[Layout, validate.Analysis]:
    """One step at a time - the one that raises the score most and still
    validates - until nothing fits or helps: add a factory (a copy of one
    per made product), an extractor (a copy, heads included) or an extractor
    head. Stage balance is `rebalance`'s job."""
    score = _score(static, analysis, product, interval_hours)
    for _ in range(max_steps):
        moves = []
        seen: set = set()
        for i, p in enumerate(layout.pins, start=1):
            kind = _kind(static, p.type_id)
            if kind in C.FACTORY_KINDS and p.product not in seen:
                seen.add(p.product)
                moves.append(lambda i=i, lay=layout: _add_factory(static, lay, i))
            elif kind == C.KIND_ECU:
                moves.append(lambda i=i, lay=layout: _add_head(lay, i, yield_per_head))
                if ("ecu", p.product) not in seen:
                    seen.add(("ecu", p.product))
                    moves.append(lambda i=i, lay=layout: _add_factory(static, lay, i))
        best = None
        for move in moves:
            cand = move()
            if cand is None:
                continue
            cand, cand_analysis = settle(static, cand, radius_km, yield_per_head, local)
            if not cand_analysis.ok:
                continue
            cand_score = _score(static, cand_analysis, product, interval_hours)
            if cand_score > score and (best is None or cand_score > best[0]):
                best = (cand_score, cand, cand_analysis)
        if best is None:
            break
        score, layout, analysis = best
    return layout, analysis


# ------------------------------------------------------------ selection
def _distance(static: StaticData, ref: Reference, design: Design) -> int:
    """How far a reference's structure counts are from the design's: the
    closest ones are analysed first (MAX_ANALYSED)."""
    got = design_of(static, ref.layout, design.chain, design.product_type_id)
    return (abs(got.total_factories - design.total_factories)
            + abs(got.total_heads - design.total_heads)
            + abs(got.launchpads + got.storages - design.launchpads - design.storages))


def from_references(static: StaticData, design: Design, planet_type_id: int, radius_km: float,
                    yield_per_head: float, interval_hours: float, comment: str = "",
                    references: Optional[list[Reference]] = None) -> Optional[ReferenceResult]:
    refs = load() if references is None else references
    # What the colony extracts or makes may not be hauled in - otherwise the
    # throughput LP would feed a reference's factories from the launchpad.
    local = self_supplied(static, design)
    ranked = sorted((r for r in refs if r.chain == design.chain and r.cc_level <= design.cc_level),
                    key=lambda r: (_distance(static, r, design), -r.upvotes))
    product = design.product_type_id

    def out(a: validate.Analysis) -> float:
        return effective_output(a, product, interval_hours)

    def balanced(layout: Layout, analysis: validate.Analysis) -> tuple[Layout, validate.Analysis]:
        cand, cand_analysis = settle(static, rebalance(static, layout, design), radius_km, yield_per_head, local)
        if cand_analysis.ok and out(cand_analysis) >= out(analysis):
            return cand, cand_analysis
        return layout, analysis

    candidates = []
    for ref in ranked:
        if len(candidates) >= MAX_ANALYSED:
            break
        layout = adapt(static, ref, design, planet_type_id, radius_km, yield_per_head, comment)
        if layout is None:
            continue
        layout, analysis = settle(static, layout, radius_km, yield_per_head, local)
        if not analysis.ok:
            continue
        layout, analysis = balanced(layout, analysis)
        if out(analysis) > 0:
            candidates.append((ref, layout, analysis))
    candidates.sort(key=lambda c: -out(c[2]))
    best: Optional[ReferenceResult] = None
    for ref, layout, analysis in candidates[:MAX_GROWN]:
        layout, analysis = prune(static, layout, analysis, product, radius_km, yield_per_head, interval_hours, local)
        # Growing can add intermediate factories the last step then cannot
        # use; balance again and grow into whatever that frees.
        for _ in range(2):
            layout, analysis = grow(static, layout, analysis, product, radius_km, yield_per_head, interval_hours, local)
            layout, analysis = balanced(layout, analysis)
        if best is None or out(analysis) > best.effective_output + 1e-9:
            best = ReferenceResult(layout, analysis, design_of(static, layout, design.chain, product), ref, out(analysis))
    return best

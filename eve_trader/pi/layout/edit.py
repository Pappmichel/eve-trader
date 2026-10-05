"""In-place layout edits for the editor (docs/PI_PLAN.md 6A.2 phase 5b).

Rules taken from Eve-PI's editor: a refused edit changes nothing (the input
layout is returned untouched and the reason raised), and an edit keeps
everything the player placed by hand - only the structures the edit touches
change. The server returns the full new layout, never a patch, so the
browser and the server cannot drift (PI_TECHNICAL_DESIGN 5).

Operations (a dict with "op"):
  move          {pin, la, lo}                  move one structure
  remove        {pin}                          remove it with its links and routes
  add           {kind, product?, heads?, la, lo}  add a structure; it is linked to the
                                               nearest hub/Advanced/High-Tech structure
                                               (Basic facilities and extractors to a hub)
                                               and gets the routes it needs
  link_level    {link, level}                  set a link's upgrade level (0-5)
  route_storage {}                             link and route every hub to the factories
                                               it can feed (for imported layouts without routes)
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .. import constants as C
from ..model import StaticData, factory_kind_for_tier
from .geometry import central_angle
from .template_io import Layout, Link, Pin, Route

MAX_LINK_LEVEL = 5


class EditError(ValueError):
    pass


def _kind(static: StaticData, pin: Pin) -> Optional[str]:
    spec = static.structures.get(pin.type_id)
    return spec.kind if spec else None


def _adjacency(layout: Layout) -> dict[int, set[int]]:
    adj: dict[int, set[int]] = {i: set() for i in range(1, len(layout.pins) + 1)}
    for lk in layout.links:
        adj[lk.a].add(lk.b)
        adj[lk.b].add(lk.a)
    return adj


def _shortest_path(layout: Layout, src: int, dst: int) -> Optional[list[int]]:
    adj = _adjacency(layout)
    prev = {src: None}
    queue = [src]
    while queue:
        cur = queue.pop(0)
        if cur == dst:
            break
        for nxt in sorted(adj.get(cur, ())):
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    if dst not in prev:
        return None
    path = [dst]
    while prev[path[-1]] is not None:
        path.append(prev[path[-1]])
    return list(reversed(path))


def _hubs(static: StaticData, layout: Layout) -> list[int]:
    return [i for i, p in enumerate(layout.pins, start=1) if _kind(static, p) in C.HUB_KINDS]


def _nearest(static: StaticData, layout: Layout, la: float, lo: float, kinds: tuple, exclude: int = 0) -> Optional[int]:
    best, best_d = None, None
    for i, p in enumerate(layout.pins, start=1):
        if i == exclude or _kind(static, p) not in kinds:
            continue
        d = central_angle(la, lo, p.la, p.lo)
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def apply(static: StaticData, layout: Layout, op: dict) -> Layout:
    name = op.get("op")
    if name == "move":
        return _move(layout, op)
    if name == "remove":
        return _reconnect(static, _remove(layout, int(op.get("pin", 0))))
    if name == "add":
        return _add(static, layout, op)
    if name == "link_level":
        return _link_level(layout, op)
    if name == "route_storage":
        return route_storage(static, layout)
    raise EditError(f"Unknown edit {name!r}")


def _pin_index(layout: Layout, pin: int) -> int:
    if not (1 <= pin <= len(layout.pins)):
        raise EditError(f"Structure {pin} does not exist")
    return pin


def _move(layout: Layout, op: dict) -> Layout:
    i = _pin_index(layout, int(op.get("pin", 0)))
    try:
        la, lo = float(op["la"]), float(op["lo"])
    except (KeyError, TypeError, ValueError) as e:
        raise EditError("A move needs la and lo") from e
    if not (0.0 < la < 3.14159) or not (-7.0 < lo < 7.0):
        raise EditError("Position out of range")
    pins = list(layout.pins)
    pins[i - 1] = replace(pins[i - 1], la=round(la, 5), lo=round(lo, 5))
    return replace(layout, pins=tuple(pins))


def _remove(layout: Layout, pin: int) -> Layout:
    _pin_index(layout, pin)

    def remap(x: int) -> int:
        return x - 1 if x > pin else x

    pins = tuple(p for k, p in enumerate(layout.pins, start=1) if k != pin)
    links = tuple(Link(remap(lk.a), remap(lk.b), lk.level) for lk in layout.links if pin not in (lk.a, lk.b))
    routes = tuple(Route(tuple(remap(x) for x in r.path), r.quantity, r.commodity)
                   for r in layout.routes if pin not in r.path)
    return replace(layout, pins=pins, links=links, routes=routes)


def _reconnect(static: StaticData, layout: Layout) -> Layout:
    """After a removal: link every structure that lost its connection to the
    hubs to the nearest connected hub/Advanced/High-Tech structure (Basic
    facilities and extractors to a hub), then fill the routes it lost."""
    hubs = _hubs(static, layout)
    if not hubs:
        return layout
    adj = _adjacency(layout)
    connected = set()
    queue = list(hubs)
    while queue:
        cur = queue.pop()
        if cur in connected:
            continue
        connected.add(cur)
        queue.extend(adj.get(cur, ()))
    orphans = [i for i in range(1, len(layout.pins) + 1) if i not in connected]
    if not orphans:
        return layout
    links = list(layout.links)
    for i in orphans:
        p = layout.pins[i - 1]
        kind = _kind(static, p)
        parent_kinds = C.HUB_KINDS if kind in (C.KIND_BASIC, C.KIND_ECU) else C.HUB_KINDS + (C.KIND_ADVANCED, C.KIND_HIGH_TECH)
        best, best_d = None, None
        for j in connected:
            if _kind(static, layout.pins[j - 1]) not in parent_kinds:
                continue
            d = central_angle(p.la, p.lo, layout.pins[j - 1].la, layout.pins[j - 1].lo)
            if best_d is None or d < best_d:
                best, best_d = j, d
        if best is not None:
            links.append(Link(i, best, 0))
            connected.add(i)
    relinked = replace(layout, links=tuple(links))
    rerouted = route_storage(static, relinked)
    # extractors that lost their output route
    routes = list(rerouted.routes)
    have_out = {(r.path[0], r.commodity) for r in routes}
    for i in orphans:
        p = relinked.pins[i - 1]
        if _kind(static, p) == C.KIND_ECU and p.product and (i, p.product) not in have_out:
            paths = [pth for pth in (_shortest_path(relinked, i, h) for h in hubs) if pth]
            if paths:
                routes.append(Route(tuple(min(paths, key=len)), float(max(1, p.heads * 2000)), p.product))
    return replace(rerouted, routes=tuple(routes))


def _link_level(layout: Layout, op: dict) -> Layout:
    k = int(op.get("link", 0))
    level = int(op.get("level", 0))
    if not (1 <= k <= len(layout.links)):
        raise EditError(f"Link {k} does not exist")
    if not (0 <= level <= MAX_LINK_LEVEL):
        raise EditError(f"Link level must be 0-{MAX_LINK_LEVEL}")
    links = list(layout.links)
    links[k - 1] = replace(links[k - 1], level=level)
    return replace(layout, links=tuple(links))


def _factory_routes(static: StaticData, layout: Layout, pin: int, home: int) -> list[Route]:
    p = layout.pins[pin - 1]
    s = static.schematic_by_output.get(p.product) if p.product else None
    if s is None:
        return []
    routes = []
    for t, q in s.inputs:
        path = _shortest_path(layout, home, pin)
        if path is None:
            raise EditError("The new structure is not connected to a hub")
        routes.append(Route(tuple(path), float(q), t))
    path = _shortest_path(layout, pin, home)
    routes.append(Route(tuple(path), float(s.output_qty), s.output_type_id))
    return routes


def _add(static: StaticData, layout: Layout, op: dict) -> Layout:
    kind = op.get("kind")
    if kind not in (C.KIND_LAUNCHPAD, C.KIND_STORAGE, C.KIND_ECU) + C.FACTORY_KINDS:
        raise EditError(f"Cannot add a {kind!r}")
    try:
        la, lo = round(float(op["la"]), 5), round(float(op["lo"]), 5)
    except (KeyError, TypeError, ValueError) as e:
        raise EditError("A new structure needs la and lo") from e
    spec = static.structure(kind, layout.planet_type_id)
    if spec is None:
        raise EditError(f"There is no {kind.replace('_', ' ')} on this planet type")
    product = op.get("product")
    heads = int(op.get("heads") or 0)
    if kind in C.FACTORY_KINDS:
        if product is None or static.schematic_by_output.get(int(product)) is None:
            raise EditError("A factory needs a product")
        if factory_kind_for_tier(static.tier(int(product)) or 0) != kind:
            raise EditError(f"{static.name(int(product))} is not made in that facility")
    if kind == C.KIND_ECU:
        if product is None or int(product) not in C.PLANET_RESOURCES.get(layout.planet_type_id, ()):
            raise EditError("An extractor needs a P0 resource of this planet type")
        if not (1 <= heads <= C.MAX_EXTRACTOR_HEADS):
            raise EditError(f"An extractor needs 1-{C.MAX_EXTRACTOR_HEADS} heads")
    new_pin = Pin(spec.type_id, la, lo, int(product) if product is not None else None, heads)
    parent_kinds = C.HUB_KINDS if kind in (C.KIND_BASIC, C.KIND_ECU) else C.HUB_KINDS + (C.KIND_ADVANCED, C.KIND_HIGH_TECH)
    parent = _nearest(static, layout, la, lo, parent_kinds)
    pins = layout.pins + (new_pin,)
    idx = len(pins)
    links = layout.links + ((Link(idx, parent, 0),) if parent else ())
    grown = replace(layout, pins=pins, links=links)
    if parent is None:
        return grown
    home = parent if _kind(static, layout.pins[parent - 1]) in C.HUB_KINDS else (
        _nearest(static, layout, la, lo, C.HUB_KINDS) or parent)
    routes = list(grown.routes)
    if kind in C.FACTORY_KINDS:
        routes += _factory_routes(static, grown, idx, home)
    elif kind == C.KIND_ECU:
        path = _shortest_path(grown, idx, home)
        if path:
            routes.append(Route(tuple(path), float(max(1, heads * 2000)), int(product)))
    return replace(grown, routes=tuple(routes))


def route_storage(static: StaticData, layout: Layout) -> Layout:
    """For every factory without an input route for one of its inputs, route
    that input from the nearest connected hub; route outputs without a route
    to the nearest connected hub. Existing routes stay as they are."""
    routes = list(layout.routes)
    hubs = _hubs(static, layout)
    if not hubs:
        raise EditError("There is no launchpad or storage facility to route from")
    have_in = {(r.path[-1], r.commodity) for r in routes}
    have_out = {(r.path[0], r.commodity) for r in routes}
    for i, p in enumerate(layout.pins, start=1):
        if _kind(static, p) not in C.FACTORY_KINDS or not p.product:
            continue
        s = static.schematic_by_output.get(p.product)
        if s is None:
            continue
        paths = [pth for pth in (_shortest_path(layout, h, i) for h in hubs) if pth]
        if not paths:
            continue
        path = min(paths, key=len)
        for t, q in s.inputs:
            if (i, t) not in have_in:
                routes.append(Route(tuple(path), float(q), t))
        if (i, s.output_type_id) not in have_out:
            routes.append(Route(tuple(reversed(path)), float(s.output_qty), s.output_type_id))
    return replace(layout, routes=tuple(routes))

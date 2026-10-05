"""Retarget a layout: same geometry, another planet type and/or product
(docs/PI_PLAN.md 6.4). Like Eve-PI's mixed-P2 idea, only structure ids,
factory schematics and routed commodities change - pins, links and route
paths stay. The caller re-validates: a bigger planet makes the same links
longer and more expensive.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .. import constants as C
from ..model import StaticData
from .template_io import Layout


class RetargetError(ValueError):
    pass


def _top_product(static: StaticData, layout: Layout) -> Optional[int]:
    made = [p.product for p in layout.pins
            if p.product is not None and static.structures.get(p.type_id)
            and static.structures[p.type_id].kind in C.FACTORY_KINDS]
    if not made:
        return None
    return max(made, key=lambda t: (static.tier(t) or 0, t))


def retarget(static: StaticData, layout: Layout, planet_type_id: Optional[int] = None,
             product_type_id: Optional[int] = None) -> Layout:
    result = layout
    if product_type_id is not None:
        result = _retarget_product(static, result, int(product_type_id))
    if planet_type_id is not None:
        result = _retarget_planet(static, result, int(planet_type_id))
    return result


def _retarget_planet(static: StaticData, layout: Layout, planet_type_id: int) -> Layout:
    if planet_type_id not in C.PLANET_TYPE_IDS:
        raise RetargetError("Unknown planet type")
    pins = []
    resources = C.PLANET_RESOURCES[planet_type_id]
    for i, p in enumerate(layout.pins, start=1):
        spec = static.structures.get(p.type_id)
        if spec is None:
            raise RetargetError(f"Pin {i}: unknown structure {p.type_id}")
        target = static.structure(spec.kind, planet_type_id)
        if target is None:
            raise RetargetError(f"Pin {i}: there is no {spec.name.split(' ', 1)[-1]} on {C.PLANET_TYPES[planet_type_id]} planets")
        if spec.kind == C.KIND_ECU and p.product is not None and p.product not in resources:
            raise RetargetError(f"{static.name(p.product)} does not occur on {C.PLANET_TYPES[planet_type_id]} planets")
        pins.append(replace(p, type_id=target.type_id))
    return replace(layout, pins=tuple(pins), planet_type_id=planet_type_id)


def _retarget_product(static: StaticData, layout: Layout, new_product: int) -> Layout:
    old_product = _top_product(static, layout)
    if old_product is None:
        raise RetargetError("This layout makes nothing - no product to replace")
    if old_product == new_product:
        return layout
    if static.tier(new_product) != static.tier(old_product):
        raise RetargetError(f"{static.name(new_product)} is not the same tier as {static.name(old_product)}")
    old_s = static.schematic_by_output[old_product]
    new_s = static.schematic_by_output.get(new_product)
    if new_s is None:
        raise RetargetError(f"No schematic makes {static.name(new_product)}")
    made_here = {p.product for p in layout.pins if p.product is not None
                 and static.structures.get(p.type_id) and static.structures[p.type_id].kind in C.FACTORY_KINDS}
    if made_here - {old_product}:
        raise RetargetError("Only single-stage colonies (one product made on the planet) can switch product; "
                            "generate a new layout for multi-stage chains")
    if len(old_s.inputs) != len(new_s.inputs):
        raise RetargetError(f"{static.name(new_product)} needs {len(new_s.inputs)} inputs, "
                            f"this layout routes {len(old_s.inputs)}")
    # Inputs are matched in order of quantity then id; the same slot keeps
    # its routes (and their per-cycle quantity is replaced by the new recipe's).
    old_inputs = sorted(old_s.inputs, key=lambda iq: (-iq[1], iq[0]))
    new_inputs = sorted(new_s.inputs, key=lambda iq: (-iq[1], iq[0]))
    mapping = {old_product: new_product}
    qty = {new_product: float(new_s.output_qty)}
    for (o, _oq), (n, nq) in zip(old_inputs, new_inputs):
        mapping[o] = n
        qty[n] = float(nq)
    for i, p in enumerate(layout.pins, start=1):
        spec = static.structures.get(p.type_id)
        if spec is not None and spec.kind == C.KIND_ECU and p.product in mapping:
            if mapping[p.product] not in C.PLANET_RESOURCES.get(layout.planet_type_id, ()):
                raise RetargetError(f"{static.name(mapping[p.product])} does not occur on this planet type")
    pins = tuple(replace(p, product=mapping.get(p.product, p.product)) if p.product is not None else p
                 for p in layout.pins)
    routes = tuple(replace(r, commodity=mapping[r.commodity], quantity=qty.get(mapping[r.commodity], r.quantity))
                   if r.commodity in mapping else r for r in layout.routes)
    comment = layout.comment.replace(static.name(old_product), static.name(new_product)) if layout.comment else ""
    return replace(layout, pins=pins, routes=routes, comment=comment)

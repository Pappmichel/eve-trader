"""StaticData from the sde_pi_* tables - built once, cached, invalidated on
SDE apply (docs/PI_TECHNICAL_DESIGN.md 3.1, P-11).

`build_static(rows)` is pure (tests feed it rows directly); `get_static()`
reads storage and caches the result behind a lock.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from .. import storage
from . import constants as C
from .model import Commodity, LinkSpec, Schematic, StaticData, StructureSpec

log = logging.getLogger("eve_trader.pi.static")

_lock = threading.Lock()
_cached: Optional[StaticData] = None
_listeners: list = []


class PiDataMissing(RuntimeError):
    """The sde_pi_* tables are empty - the SDE refresh has not run since the
    PI tool was deployed (P-21). actions.py turns this into an ActionError."""


def on_invalidate(fn) -> None:
    """Register a cache-clear callback (the design cache in engine.py)."""
    _listeners.append(fn)


def invalidate() -> None:
    global _cached
    with _lock:
        _cached = None
    for fn in list(_listeners):
        try:
            fn()
        except Exception:  # noqa: BLE001 - a listener must not break an SDE apply
            log.exception("PI cache invalidation listener failed")


def get_static() -> StaticData:
    global _cached
    with _lock:
        if _cached is None:
            _cached = build_static(storage.load_pi_static_rows())
        return _cached


def _derive_tiers(schematic_types: list[tuple]) -> dict[int, int]:
    """P0 = inputs that are never outputs; tier(x) = 1 + max(tier(inputs)).
    The max matters: P4 recipes with a P1 input stay P4 (P-13)."""
    inputs_of: dict[int, list[int]] = {}
    outputs: set[int] = set()
    inputs: set[int] = set()
    by_schematic: dict[int, dict[str, list[int]]] = {}
    for schematic_id, type_id, _qty, is_input in schematic_types:
        entry = by_schematic.setdefault(int(schematic_id), {"in": [], "out": []})
        entry["in" if is_input else "out"].append(int(type_id))
    for entry in by_schematic.values():
        for out in entry["out"]:
            outputs.add(out)
            inputs_of[out] = list(entry["in"])
        inputs.update(entry["in"])
    tiers = {t: 0 for t in inputs - outputs}
    changed = True
    while changed:
        changed = False
        for out, ins in inputs_of.items():
            if out in tiers or not all(i in tiers for i in ins):
                continue
            tiers[out] = 1 + max(tiers[i] for i in ins)
            changed = True
    return tiers


def build_static(rows: dict[str, list[tuple]]) -> StaticData:
    if not rows.get("schematics") or not rows.get("attributes"):
        raise PiDataMissing("PI data missing - run the SDE refresh in Admin first.")

    attrs: dict[int, dict[int, float]] = {}
    for type_id, attr_id, value in rows["attributes"]:
        attrs.setdefault(int(type_id), {})[int(attr_id)] = float(value)
    types = {
        int(t): {"name": name, "group": g, "category": cat, "volume": vol}
        for t, name, g, cat, vol in rows["types"]
    }
    tiers = _derive_tiers(rows["schematic_types"])

    commodities: dict[int, Commodity] = {}
    for type_id, tier in tiers.items():
        info = types.get(type_id, {})
        a = attrs.get(type_id, {})
        export_base = a.get(C.ATTR_EXPORT_TAX_BASE)
        import_base = a.get(C.ATTR_IMPORT_TAX_BASE)
        if export_base is None:
            # Data error, not a default - logged and taxed as 0 is wrong, so
            # the commodity is still listed but marked by a None-safe 0 and a
            # log line (P-20). Real SDE data has it for every commodity.
            log.warning("PI commodity %s has no export tax base", type_id)
        commodities[type_id] = Commodity(
            type_id=type_id,
            name=info.get("name") or str(type_id),
            tier=tier,
            volume=round(float(info.get("volume") or 0.0), 4),
            export_tax_base=float(export_base or 0.0),
            import_tax_base=float(import_base if import_base is not None else (export_base or 0.0)),
        )

    pins_by_schematic: dict[int, set[int]] = {}
    for schematic_id, pin_type_id in rows["schematic_pins"]:
        pins_by_schematic.setdefault(int(schematic_id), set()).add(int(pin_type_id))
    st_by_schematic: dict[int, list[tuple]] = {}
    for schematic_id, type_id, qty, is_input in rows["schematic_types"]:
        st_by_schematic.setdefault(int(schematic_id), []).append((int(type_id), int(qty), bool(is_input)))

    schematics: dict[int, Schematic] = {}
    schematic_by_output: dict[int, Schematic] = {}
    for schematic_id, name, cycle in rows["schematics"]:
        entries = st_by_schematic.get(int(schematic_id), [])
        outs = [(t, q) for t, q, is_in in entries if not is_in]
        if len(outs) != 1:
            log.warning("PI schematic %s has %d outputs - skipped", schematic_id, len(outs))
            continue
        s = Schematic(
            schematic_id=int(schematic_id),
            name=name,
            cycle_seconds=int(cycle),
            output_type_id=outs[0][0],
            output_qty=outs[0][1],
            inputs=tuple(sorted((t, q) for t, q, is_in in entries if is_in)),
            pin_type_ids=frozenset(pins_by_schematic.get(int(schematic_id), set())),
        )
        schematics[s.schematic_id] = s
        schematic_by_output[s.output_type_id] = s

    # Processor kind from the highest tier its schematics produce.
    pin_max_tier: dict[int, int] = {}
    for s in schematics.values():
        tier = tiers.get(s.output_type_id, 0)
        for pin in s.pin_type_ids:
            pin_max_tier[pin] = max(pin_max_tier.get(pin, 0), tier)

    structures: dict[int, StructureSpec] = {}
    structure_ids: dict[tuple[str, int], int] = {}
    link: Optional[LinkSpec] = None
    head_cpu, head_power = 110.0, 550.0
    decay_factor, noise_factor = 0.012, 0.8
    for type_id, info in types.items():
        group = info.get("group")
        a = attrs.get(type_id, {})
        kind = None
        if group == C.GROUP_COMMAND_CENTER:
            kind = C.KIND_COMMAND_CENTER
        elif group == C.GROUP_ECU:
            kind = C.KIND_ECU
        elif group == C.GROUP_STORAGE:
            kind = C.KIND_STORAGE
        elif group == C.GROUP_SPACEPORT:
            kind = C.KIND_LAUNCHPAD
        elif group == C.GROUP_PROCESSOR:
            tier = pin_max_tier.get(type_id)
            if tier == 1:
                kind = C.KIND_BASIC
            elif tier in (2, 3):
                kind = C.KIND_ADVANCED
            elif tier == 4:
                kind = C.KIND_HIGH_TECH
        elif group == C.GROUP_LINK:
            link = LinkSpec(
                cpu_base=a.get(C.ATTR_CPU_LOAD, 15.0),
                power_base=a.get(C.ATTR_POWER_LOAD, 10.0),
                cpu_per_km=a.get(C.ATTR_CPU_PER_KM, 0.2),
                power_per_km=a.get(C.ATTR_POWER_PER_KM, 0.15),
                cpu_level_modifier=a.get(C.ATTR_CPU_LEVEL_MODIFIER, 1.4),
                power_level_modifier=a.get(C.ATTR_POWER_LEVEL_MODIFIER, 1.2),
                capacity=a.get(C.ATTR_LOGISTICAL_CAPACITY, 1250.0),
            )
            continue
        if kind is None:
            continue
        planet = a.get(C.ATTR_PLANET_RESTRICTION)
        planet_type_id = int(planet) if planet else None
        if kind == C.KIND_COMMAND_CENTER:
            cpu, power = a.get(C.ATTR_CPU_OUTPUT, 0.0), a.get(C.ATTR_POWER_OUTPUT, 0.0)
        else:
            cpu, power = a.get(C.ATTR_CPU_LOAD, 0.0), a.get(C.ATTR_POWER_LOAD, 0.0)
        if kind == C.KIND_ECU:
            head_cpu = a.get(C.ATTR_HEAD_CPU, head_cpu)
            head_power = a.get(C.ATTR_HEAD_POWER, head_power)
            decay_factor = a.get(C.ATTR_ECU_DECAY_FACTOR, decay_factor)
            noise_factor = a.get(C.ATTR_ECU_NOISE_FACTOR, noise_factor)
        spec = StructureSpec(
            type_id=type_id, name=info.get("name") or str(type_id), kind=kind,
            planet_type_id=planet_type_id, cpu=float(cpu), power=float(power),
            capacity=float(a.get(C.ATTR_TYPE_CAPACITY) or a.get(C.ATTR_CAPACITY) or 0.0),
            isk_cost=float(a.get(C.ATTR_BASE_PRICE, 0.0)),
        )
        structures[type_id] = spec
        if planet_type_id is not None:
            key = (kind, planet_type_id)
            current = structures.get(structure_ids.get(key)) if key in structure_ids else None
            if kind == C.KIND_COMMAND_CENTER:
                # The SDE also lists one unpublished Command Center type per
                # upgrade level ("Limited/Standard/.../Elite Barren Command
                # Center", CPU 7057..25415). The placeable one is level 0,
                # i.e. the one with the smallest CPU output.
                better = current is None or (spec.cpu, type_id) < (current.cpu, current.type_id)
            else:
                # Lowest type id wins if the SDE ever lists two (stable choice).
                better = current is None or type_id < current.type_id
            if better:
                structure_ids[key] = type_id

    if link is None:
        link = LinkSpec(15.0, 10.0, 0.2, 0.15, 1.4, 1.2, 1250.0)
    return StaticData(
        commodities=commodities, schematics=schematics, schematic_by_output=schematic_by_output,
        structures=structures, link=link, head_cpu=head_cpu, head_power=head_power,
        decay_factor=decay_factor, noise_factor=noise_factor, structure_ids=structure_ids,
    )

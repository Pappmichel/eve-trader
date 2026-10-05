"""PI design tools (phase 5b/5c) - actions for the router and the CLI.

Ways to build a product, partial sourcing, mixed P2, storage suggestion,
grow to supply, and in-place layout edits. Shares the helpers of
pi/actions.py (static data, planet, settings, prices, result shapes).
"""
from __future__ import annotations

from typing import Any, Optional

from ..actions import ActionError
from . import actions as base
from . import economics as econ
from . import engine, variants
from .config import PI_CONFIG, PiConfig
from .model import CHAINS


def _context(planet_id, planet_type_id, radius_km, zone, cc_level, cfg, yield_per_head=None,
             program_hours=None, interval_hours=None):
    static = base._static()
    planet, planet_zone, _system = base._planet_from(static, planet_id, planet_type_id, radius_km)
    z = base._zone_or_default(zone or planet_zone, cfg)
    lv = base._cc_level(cc_level, cfg)
    a = base._assumptions(cfg, z, yield_per_head, program_hours, interval_hours)
    return static, planet, z, lv, a


def _with_economics(static, ev, prices, m) -> Optional[dict]:
    if ev is None:
        return None
    return {"evaluation": base.evaluation_dict(static, ev),
            "economics": base._economics_dict(econ.compute(ev, static, prices, m), static)}


def do_ways_to_build(product_type_id: int, planet_id: Optional[int] = None, planet_type_id: Optional[int] = None,
                     radius_km: Optional[float] = None, zone: Optional[str] = None, cc_level: Optional[int] = None,
                     cfg: PiConfig = PI_CONFIG) -> dict:
    """Every split of the product's inputs into made here / hauled in, plus
    (for a P2) partial sourcing with one P1 extracted - each costed."""
    static, planet, z, lv, a = _context(planet_id, planet_type_id, radius_km, zone, cc_level, cfg)
    product = int(product_type_id)
    if product not in static.commodities or (static.tier(product) or 0) < 2:
        raise ActionError("Choose a P2-P4 product")
    m = base._market(cfg, z, assumptions=a)
    prices = base._prices(static, cfg)
    rows = []
    for v in variants.ways_to_build(static, planet, product, lv, a):
        rows.append({
            "made": [{"type_id": t, "name": static.name(t)} for t in v["made"]],
            "hauled": [{"type_id": t, "name": static.name(t)} for t in v["hauled"]],
            **(_with_economics(static, v["evaluation"], prices, m) or {"evaluation": None, "economics": None}),
        })
    partial = []
    for v in variants.partial_p0_p2(static, planet, product, lv, a):
        partial.append({
            "extracted": {"type_id": v["extracted_p1"], "name": static.name(v["extracted_p1"])},
            "hauled": [{"type_id": t, "name": static.name(t)} for t in v["hauled"]],
            **(_with_economics(static, v["evaluation"], prices, m) or {"evaluation": None, "economics": None}),
        })
    if not rows and not partial:
        raise ActionError("This planet type cannot build that product")
    rows.sort(key=lambda r: -((r.get("economics") or {}).get("profit_per_day") or -1e18))
    return {"product": {"type_id": product, "name": static.name(product)}, "zone": z, "cc_level": lv,
            "variants": rows, "partial": partial}


def do_mixed_p2(assignments: dict, planet_id: Optional[int] = None, planet_type_id: Optional[int] = None,
                radius_km: Optional[float] = None, zone: Optional[str] = None, cc_level: Optional[int] = None,
                launchpads: int = 1, storages: int = 0, cfg: PiConfig = PI_CONFIG) -> dict:
    static, planet, z, lv, a = _context(planet_id, planet_type_id, radius_km, zone, cc_level, cfg)
    try:
        parsed = {int(k): int(v) for k, v in (assignments or {}).items()}
        if sum(parsed.values()) > 60 or not (1 <= int(launchpads) <= 10) or not (0 <= int(storages) <= 10):
            raise ValueError("too many structures")
        ev = variants.mixed_p2(static, planet, parsed, lv, a, int(launchpads), int(storages))
    except ValueError as e:
        raise ActionError(str(e)) from e
    m = base._market(cfg, z, assumptions=a)
    return {"zone": z, "cc_level": lv, **_with_economics(static, ev, base._prices(static, cfg), m)}


def _design(static, planet, chain, product, lv, design: Optional[dict], a):
    if chain not in CHAINS:
        raise ActionError(f"Unknown chain {chain!r}")
    if design:
        return base._design_from_input(static, planet, chain, int(product), lv, design)
    ev, why = engine.best_design(static, planet, chain, int(product), lv, a, exact_radius=True)
    if ev is None:
        raise ActionError(why or "Nothing fits")
    return ev.design


def do_storage_suggestion(chain: str, product_type_id: int, planet_id: Optional[int] = None,
                          planet_type_id: Optional[int] = None, radius_km: Optional[float] = None,
                          zone: Optional[str] = None, cc_level: Optional[int] = None,
                          interval_hours: Optional[float] = None, design: Optional[dict] = None,
                          cfg: PiConfig = PI_CONFIG) -> dict:
    static, planet, z, lv, a = _context(planet_id, planet_type_id, radius_km, zone, cc_level, cfg,
                                        interval_hours=interval_hours)
    d = _design(static, planet, chain, product_type_id, lv, design, a)
    return {"interval_hours": a.interval_hours, **variants.storage_suggestion(static, planet, d, a)}


def do_grow_to_supply(chain: str, product_type_id: int, planet_id: Optional[int] = None,
                      planet_type_id: Optional[int] = None, radius_km: Optional[float] = None,
                      zone: Optional[str] = None, cc_level: Optional[int] = None,
                      yield_per_head: Optional[float] = None, design: Optional[dict] = None,
                      cfg: PiConfig = PI_CONFIG) -> dict:
    static, planet, z, lv, a = _context(planet_id, planet_type_id, radius_km, zone, cc_level, cfg,
                                        yield_per_head=yield_per_head)
    d = _design(static, planet, chain, product_type_id, lv, design, a)
    ev = variants.grow_to_supply(static, planet, d, a)
    return {"design": ev.design.to_dict(), "evaluation": base.evaluation_dict(static, ev)}


def do_edit_layout(template: Any, edit: dict, planet_id: Optional[int] = None,
                   radius_km: Optional[float] = None, cfg: PiConfig = PI_CONFIG) -> dict:
    """Apply one editor operation; a refused edit raises and changes nothing."""
    from .layout import edit as edit_mod
    from .layout import validate

    static = base._static()
    layout = base._parse_template(template)
    if not isinstance(edit, dict):
        raise ActionError("An edit is an object with an \"op\"")
    try:
        new_layout = edit_mod.apply(static, layout, edit)
    except edit_mod.EditError as e:
        raise ActionError(str(e)) from e
    y = base._assumptions(cfg, cfg.pi_zone).effective_yield
    a = validate.analyse(static, new_layout, base._radius_for(planet_id, radius_km), y)
    return base._layout_payload(static, new_layout, a)

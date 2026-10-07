"""PI chain planner action (docs/PI_PLAN.md 3.4/3.5): one target product
across the planets of one system and the player's characters. Thin
orchestration around the pure `chain_plan.py`; loads the system, characters,
prices and settings through the helpers in actions.py."""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from ..actions import ActionError
from . import actions
from . import chain_plan as cp
from . import constants as C
from .config import PI_CONFIG, PiConfig
from .model import Planet

log = logging.getLogger("eve_trader.pi.chain_actions")

MAX_CHARACTERS = 30


def _characters(characters: Optional[list[dict]], cc_level: Optional[int], cfg: PiConfig) -> list[cp.CharacterSpec]:
    """Given characters, else token characters (ESI skills or manual values),
    else the manual pi_characters x pi_planets_per_character. `cc_level`
    overrides every character's Command Center level."""
    override = None if cc_level is None else actions._cc_level(cc_level, cfg)
    specs: list[cp.CharacterSpec] = []
    if characters:
        if len(characters) > MAX_CHARACTERS:
            raise ActionError(f"At most {MAX_CHARACTERS} characters")
        for i, c in enumerate(characters, start=1):
            cid = c.get("character_id")
            name = str(c.get("name") or c.get("character_name") or f"Character {i}")
            try:
                planets = int(c.get("planets") or cfg.pi_planets_per_character)
                lv = int(c["cc_level"]) if c.get("cc_level") is not None else int(cfg.pi_cc_level)
            except (TypeError, ValueError) as e:
                raise ActionError(f"Invalid character {name!r}: {e}") from e
            if not 1 <= planets <= cp.MAX_PLANETS_PER_CHARACTER or lv not in C.CC_LEVELS:
                raise ActionError(f"Character {name!r}: planets must be 1-{cp.MAX_PLANETS_PER_CHARACTER} "
                                  "and the Command Center level 0-5")
            specs.append(cp.CharacterSpec(str(cid) if cid is not None else f"c{i}", name, planets,
                                          override if override is not None else lv))
        return specs
    try:
        overview = actions.do_characters(cfg)
    except Exception:  # noqa: BLE001 - token store unavailable -> manual values
        log.info("PI characters unavailable, using manual settings", exc_info=True)
        overview = {}
    for c in overview.get("characters") or []:
        cid = int(c["character_id"])
        specs.append(cp.CharacterSpec(str(cid), c.get("character_name") or f"#{cid}", int(c["planets"]),
                                      override if override is not None else int(c["cc_level"])))
    if not specs:
        for i in range(1, int(cfg.pi_characters) + 1):
            specs.append(cp.CharacterSpec(f"c{i}", f"Character {i}", int(cfg.pi_planets_per_character),
                                          override if override is not None else int(cfg.pi_cc_level)))
    return specs


def _named(static, rates: dict[int, float]) -> list[dict]:
    return [{"type_id": t, "name": static.name(t), "tier": static.tier(t), "per_day": q}
            for t, q in sorted(rates.items(), key=lambda kv: (static.tier(kv[0]) or 0, kv[0]))]


def plan_dict(static, plan: cp.ChainPlan, virtual: bool, planet_types: dict[int, int]) -> dict:
    return {
        "status": plan.status,
        "mode": plan.mode,
        "target_type_id": plan.target_type_id,
        "target_name": static.name(plan.target_type_id),
        "top_types": [{"type_id": t, "name": static.name(t), "tier": static.tier(t)} for t in plan.top_type_ids],
        "target_units_per_day": plan.target_units_per_day,
        "max_target_units_per_day": plan.max_target_units_per_day,
        "requested_units_per_day": plan.requested_units_per_day,
        "profit_per_day": plan.profit_per_day,
        "profit_per_slot": plan.profit_per_slot,
        "used_slots": plan.used_slots,
        "slots": plan.slots,
        "free_slots": plan.free_slots,
        "characters": plan.characters,
        "assignments": [{
            "character_key": a.character_key, "character": a.character_name, "cc_level": a.cc_level,
            "planet_id": None if virtual else a.planet_id, "planet_name": a.planet_name,
            "planet_type_id": planet_types.get(a.planet_id),
            "planet_type": C.PLANET_TYPES.get(planet_types.get(a.planet_id, 0)),
            "chain": a.chain, "product_type_id": a.product_type_id, "product_name": static.name(a.product_type_id),
            "is_extraction": a.is_extraction, "design": a.design,
            "run_level": a.run_level, "yield_factor": a.yield_factor, "units_per_day": a.units_per_day,
            "exports": _named(static, a.exports), "imports": _named(static, a.imports),
            # Body for POST /api/pi/layouts/generate.
            "layout_request": {
                "chain": a.chain, "product_type_id": a.product_type_id, "cc_level": a.cc_level,
                "design": a.design,
                **({"planet_type_id": planet_types.get(a.planet_id)} if virtual else {"planet_id": a.planet_id}),
            },
        } for a in plan.assignments],
        "stages": [{
            "type_id": s.type_id, "name": static.name(s.type_id), "tier": s.tier, "in_tree": s.in_tree,
            "colonies": s.colonies, "made": s.made, "needed": s.needed, "internal": s.internal,
            "bought": s.bought, "sold": s.sold, "discarded": s.discarded,
        } for s in plan.stages],
        "purchases": [{
            "type_id": p.type_id, "name": static.name(p.type_id), "units_per_day": p.units_per_day,
            "cost_per_day": p.cost_per_day, "reason_code": p.reason_code, "reason": p.reason,
        } for p in plan.purchases],
        "notes": list(plan.notes),
    }


def do_chain_plan(product_type_id: int, solar_system_id: Optional[int] = None,
                  characters: Optional[list[dict]] = None, cc_level: Optional[int] = None,
                  owner_tax_rate: Optional[float] = None, allow_buy: bool = False,
                  target_per_hour: Optional[float] = None, cfg: PiConfig = PI_CONFIG) -> dict[str, Any]:
    """Plan a whole chain for one product (P1-P4) in one system: which
    character puts which colony on which planet. Without a system: a generic
    plan over one virtual planet per type (median radius, any number).

    `target_per_hour` sizes the plan to that output. None keeps the
    maximum the slots can make."""
    static = actions._static()
    product = int(product_type_id)
    if product not in static.commodities or (static.tier(product) or 0) < 1:
        raise ActionError("Choose a P1-P4 product")
    if owner_tax_rate is not None and not 0 <= float(owner_tax_rate) <= 1:
        raise ActionError("Owner tax rate must be between 0 and 1")
    target_per_day = None
    if target_per_hour is not None:
        target_per_hour = float(target_per_hour)
        if not math.isfinite(target_per_hour) or not 0 < target_per_hour <= 1_000_000:
            raise ActionError("Wanted output per hour must be a positive number up to 1,000,000")
        target_per_day = target_per_hour * 24.0
    specs = _characters(characters, cc_level, cfg)

    system_info: Optional[dict] = None
    notes: list[str] = []
    if solar_system_id is not None:
        system = actions.do_system_planets(int(solar_system_id))
        if not system["planets"]:
            raise ActionError("This system has no PI planets")
        zone = system["zone"]
        planets = [Planet(p["planet_type_id"], p["radius_km"], p["planet_id"], p["name"], int(solar_system_id))
                   for p in system["planets"]]
        system_info = {k: v for k, v in system.items() if k != "planets"}
        if not system["reachable"]:
            notes.append("This region is not reachable (Jove space).")
        virtual = False
    else:
        zone = actions._zone_or_default(None, cfg)
        medians = actions._radius_medians()
        planets = [Planet(pt, float(medians.get(pt, 5000.0)), -pt, f"{name} (any)")
                   for pt, name in sorted(C.PLANET_TYPES.items())]
        virtual = True

    a = actions._assumptions(cfg, zone)
    m = actions._market(cfg, zone, owner_tax_rate, assumptions=a)
    prices = actions._prices(static, cfg, with_history=False)
    factory_limit = None if virtual else max((s.planets for s in specs), default=1)
    options = cp.build_options(static, product, planets, sorted({s.cc_level for s in specs}), a, prices, m,
                               factory_planet_limit=factory_limit, exact_radius=False)
    market = cp.build_market(static, prices, m)
    try:
        plan = cp.plan_chain(static, product, planets, specs, options, market, allow_buy=bool(allow_buy),
                             unlimited_planets=virtual, target_per_day=target_per_day)
    except ValueError as e:
        raise ActionError(str(e)) from e
    result = plan_dict(static, plan, virtual, {p.planet_id: p.planet_type_id for p in planets})
    result["notes"] = notes + result["notes"]
    if virtual:
        result["notes"].append("No system chosen: one virtual planet per type (median radius, any number of "
                               "planets) - pick a system for a real plan.")
    result.update({
        "system": system_info, "zone": zone, "allow_buy": bool(allow_buy),
        "planets": [{"planet_id": None if virtual else p.planet_id, "name": p.name,
                     "planet_type_id": p.planet_type_id, "planet_type": C.PLANET_TYPES.get(p.planet_type_id),
                     "radius_km": p.radius_km} for p in planets],
        "assumptions": {"yield_per_head": a.yield_per_head, "program_hours": a.program_hours,
                        "interval_hours": a.interval_hours, "tax_rate": m.tax_rate},
        "price_note": prices.source_note,
    })
    return result

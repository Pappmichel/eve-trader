"""PI tool actions - the one entry point for the router and the CLI.

docs/PI_PLAN.md / docs/PI_TECHNICAL_DESIGN.md. Thin orchestration: the
logic is in engine.py (designs), economics.py (money), chains.py; this
module loads static data, settings, prices and skills and shapes results.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Optional

from .. import storage
from ..actions import ActionError
from ..config import ConfigError, save_tenant_config_overrides
from ..hubs import ALL_HUBS, hub_pricing
from . import chains as chains_mod
from . import constants as C
from . import economics as econ
from . import engine
from .config import PI_CONFIG, PiConfig, validate_pi_overrides
from .model import CHAINS, EXTRACTION_CHAINS, Design, Evaluation, Planet, StaticData
from .static import PiDataMissing, get_static

log = logging.getLogger("eve_trader.pi.actions")

TOOL_KEY = "pi"
JITA_REGION_ID = 10000002


# ------------------------------------------------------------------ helpers
def _static() -> StaticData:
    try:
        return get_static()
    except PiDataMissing as e:
        raise ActionError(str(e)) from e


def _zone_or_default(zone: Optional[str], cfg: PiConfig) -> str:
    z = zone or cfg.pi_zone
    if z not in C.ZONES:
        raise ActionError(f"Unknown security zone {z!r}")
    return z


def _cc_level(cc_level: Optional[int], cfg: PiConfig) -> int:
    lv = cfg.pi_cc_level if cc_level is None else int(cc_level)
    if lv not in C.CC_LEVELS:
        raise ActionError("Command Center level must be 0-5")
    return lv


def _assumptions(cfg: PiConfig, zone: str, yield_override: Optional[float] = None,
                 program_hours: Optional[float] = None, interval_hours: Optional[float] = None) -> engine.Assumptions:
    y = cfg.yield_for_zone(zone) if yield_override is None else float(yield_override)
    return engine.Assumptions(
        yield_per_head=y,
        program_hours=float(program_hours or cfg.pi_program_hours),
        interval_hours=float(interval_hours or cfg.pi_collection_interval_hours),
    )


def _market(cfg: PiConfig, zone: str, owner_tax_rate: Optional[float] = None,
            freight_per_m3: Optional[float] = None, customs_code_expertise: Optional[int] = None,
            assumptions: Optional[engine.Assumptions] = None) -> econ.MarketSettings:
    owner = cfg.pi_owner_tax_rate if owner_tax_rate is None else float(owner_tax_rate)
    cce = cfg.pi_customs_code_expertise_level if customs_code_expertise is None else int(customs_code_expertise)
    a = assumptions or _assumptions(cfg, zone)
    return econ.MarketSettings(
        broker_fee=cfg.pi_broker_fee_rate, sales_tax=cfg.pi_sales_tax_rate, valuation=cfg.pi_valuation,
        freight_per_m3=cfg.pi_freight_per_m3 if freight_per_m3 is None else float(freight_per_m3),
        tax_rate=econ.npc_tax_rate(zone, cce) + owner,
        amortisation_days=cfg.pi_amortisation_days, min_isk_per_planet_day=cfg.pi_min_isk_per_planet_day,
        market_share_warning=cfg.pi_market_share_warning,
        program_hours=a.program_hours, interval_hours=a.interval_hours,
    )


# Price history (trend + daily volume) changes slowly: one fetch per region
# and hour, per-key lock (CLAUDE.md caching shape 2).
_history_cache: dict[int, tuple[float, dict[int, Optional[dict]]]] = {}
_history_locks: dict[int, threading.Lock] = {}
_history_guard = threading.Lock()
_HISTORY_TTL = 3600.0


def clear_history_cache() -> None:
    with _history_guard:
        _history_cache.clear()


def _trends(static: StaticData, region_id: int) -> dict[int, Optional[dict]]:
    with _history_guard:
        lock = _history_locks.setdefault(region_id, threading.Lock())
    with lock:
        hit = _history_cache.get(region_id)
        if hit and time.time() - hit[0] < _HISTORY_TTL:
            return hit[1]
        from ..goonmetrics_client import GoonmetricsClient

        ids = sorted(t for t, c in static.commodities.items() if c.tier >= 1)
        try:
            points = GoonmetricsClient().price_history_chunked(region_id, ids, esi_for_missing=set(ids))
        except Exception:  # noqa: BLE001 - trends are optional display data
            log.exception("PI price history fetch failed")
            points = []
        by_type: dict[int, list] = {}
        for p in points:
            by_type.setdefault(p.type_id, []).append(p)
        result = {t: econ.trend_from_history(by_type.get(t, [])) for t in ids}
        _history_cache[region_id] = (time.time(), result)
        return result


def _prices(static: StaticData, cfg: PiConfig, with_history: bool = True) -> econ.Prices:
    """Order-book prices for every PI commodity at the PI hub (or the best
    hub per item), with PI's own freight rate (P-34)."""
    from ..esi_client import ESIClient

    ids = sorted(static.commodities)
    volumes = {t: c.volume for t, c in static.commodities.items()}
    try:
        hp = hub_pricing(ESIClient(), int(cfg.hub_region_id), ids, volumes, cfg.pi_broker_fee_rate,
                         cfg.pi_freight_per_m3, freight_override=cfg.pi_freight_per_m3)
    except Exception as e:  # noqa: BLE001
        raise ActionError(f"Market prices unavailable: {e}") from e
    sell = {t: (s.sell_percentile if s else None) for t, s in ((t, hp.stats.get(t)) for t in ids)}
    buy = {t: (s.buy_percentile if s else None) for t, s in ((t, hp.stats.get(t)) for t in ids)}
    trends: dict[int, Optional[dict]] = {}
    if with_history:
        region = JITA_REGION_ID if int(cfg.hub_region_id) == ALL_HUBS else int(cfg.hub_region_id)
        trends = _trends(static, region)
    daily = {t: (tr or {}).get("avg_daily_volume") for t, tr in trends.items()}
    return econ.Prices(sell=sell, buy=buy, hub_by_type=dict(hp.hub_by_type), daily_volume=daily, trend=trends)


def _planet_from(static: StaticData, planet_id: Optional[int], planet_type_id: Optional[int],
                 radius_km: Optional[float]) -> tuple[Planet, Optional[str], Optional[dict]]:
    """(planet, zone of its system or None, system info) - a real planet from
    the SDE, or a free planet type + radius."""
    if planet_id is not None:
        row = storage.get_pi_planet(int(planet_id))
        if row is None:
            raise ActionError(f"Unknown PI planet {planet_id}")
        pid, name, system_id, type_id, radius = row
        system = storage.get_solar_system(system_id)
        zone = econ.security_zone(system[2], system[3]) if system else None
        info = {"solar_system_id": system_id, "name": system[1] if system else None,
                "security": system[2] if system else None, "region_id": system[3] if system else None}
        return Planet(int(type_id), float(radius), int(pid), name, int(system_id)), zone, info
    if planet_type_id is None or int(planet_type_id) not in C.PLANET_TYPE_IDS:
        raise ActionError("Choose a planet or a PI planet type")
    if radius_km is None:
        radius_km = _radius_medians().get(int(planet_type_id), 5000.0)
    if not (50 <= float(radius_km) <= 200000):
        raise ActionError("Planet radius must be between 50 and 200,000 km")
    return Planet(int(planet_type_id), float(radius_km)), None, None


_medians_cache: Optional[dict[int, float]] = None


def _radius_medians() -> dict[int, float]:
    global _medians_cache
    if _medians_cache is None:
        _medians_cache = storage.pi_planet_radius_medians()
    return _medians_cache


def _design_dict(static: StaticData, ev: Evaluation) -> dict:
    d = ev.design
    return {
        **d.to_dict(),
        "factories_named": [
            {"type_id": t, "name": static.name(t), "tier": static.tier(t), "count": n,
             "utilization": ev.utilization.get(t)}
            for t, n in d.factories
        ],
        "ecus_named": [{"type_id": p, "name": static.name(p), "heads": h} for p, h in d.ecus],
    }


def _named_rates(static: StaticData, rates: dict[int, float]) -> list[dict]:
    return [{"type_id": t, "name": static.name(t), "tier": static.tier(t), "per_hour": q}
            for t, q in sorted(rates.items(), key=lambda kv: (static.tier(kv[0]) or 0, kv[0]))]


def evaluation_dict(static: StaticData, ev: Evaluation) -> dict:
    inf = math.isinf(ev.buffer_hours)
    return {
        "design": _design_dict(static, ev),
        "fits": ev.fits,
        "cpu_used": ev.cpu_used, "cpu_capacity": ev.cpu_capacity,
        "power_used": ev.power_used, "power_capacity": ev.power_capacity,
        "links": {"count": ev.link_count, "km": ev.link_km, "level": ev.link_level,
                  "cpu": ev.link_cpu, "power": ev.link_power},
        "product_per_hour": ev.product_per_hour,
        "effective_product_per_hour": ev.effective_product_per_hour,
        "effective_factor": ev.effective_factor,
        "buffer_hours": None if inf else ev.buffer_hours,
        "import_m3_per_hour": ev.import_m3_per_hour,
        "export_m3_per_hour": ev.export_m3_per_hour,
        "extracted": _named_rates(static, ev.extracted),
        "produced": _named_rates(static, ev.produced),
        "imports": _named_rates(static, ev.imports),
        "exports": _named_rates(static, ev.exports),
        "idle_factories": ev.idle_factories,
        "setup_isk": ev.setup_isk,
        "notes": list(ev.notes),
    }


def _economics_dict(e: econ.Economics, static: StaticData) -> dict:
    d = e.to_dict()
    d["missing_prices"] = [{"type_id": t, "name": static.name(t)} for t in e.missing_prices]
    return d


# ------------------------------------------------------------------ meta
def do_get_meta(cfg: PiConfig = PI_CONFIG) -> dict:
    """Products per tier with the chains each can be built through, planet
    types (with their P0 and whether they have High-Tech plants), zones."""
    static = _static()
    products = []
    for tier in range(1, 5):
        for t in static.products_of_tier(tier):
            chains = [ch for ch, (_s, target) in CHAINS.items() if target == tier]
            products.append({"type_id": t, "name": static.name(t), "tier": tier, "chains": chains})
    planet_types = [
        {"type_id": pt, "name": name,
         "resources": [{"type_id": r, "name": static.name(r)} for r in sorted(C.PLANET_RESOURCES[pt])],
         "high_tech": static.has_kind(C.KIND_HIGH_TECH, pt),
         "median_radius_km": _radius_medians().get(pt)}
        for pt, name in sorted(C.PLANET_TYPES.items(), key=lambda kv: kv[1])
    ]
    return {
        "products": products,
        "planet_types": planet_types,
        "chains": list(CHAINS),
        "zones": list(C.ZONES),
        "cc_levels": [{"level": lv, "cpu": v[0], "power": v[1], "upgrade_isk": v[2]} for lv, v in C.CC_LEVELS.items()],
    }


# ------------------------------------------------------------------ profitability
def _profit_rows(static: StaticData, cfg: PiConfig, zone: str, cc_level: int, prices: econ.Prices) -> list[dict]:
    a = _assumptions(cfg, zone)
    m = _market(cfg, zone, assumptions=a)
    medians = _radius_medians()
    rows: list[dict] = []
    for chain, (source, target) in CHAINS.items():
        for product in static.products_of_tier(target):
            if chain in EXTRACTION_CHAINS:
                candidates = list(C.PLANET_TYPE_IDS)
            else:
                # Factory chains: structures are the same on every planet type;
                # only High-Tech plants are restricted. One row, the first
                # planet type that can build it, at the reference radius.
                candidates = [pt for pt in (2016, 11, 13, 12, 2014, 2015, 2017, 2063)
                              if engine.check_feasible(static, pt, chain, product) is None][:1]
            for pt in candidates:
                if engine.check_feasible(static, pt, chain, product) is not None:
                    continue
                radius = medians.get(pt, cfg.pi_reference_radius_km) if chain in EXTRACTION_CHAINS \
                    else cfg.pi_reference_radius_km
                ev, why = engine.best_design(static, Planet(pt, radius), chain, product, cc_level, a)
                if ev is None:
                    continue
                e = econ.compute(ev, static, prices, m)
                rows.append({
                    "product_type_id": product, "product_name": static.name(product), "tier": target,
                    "chain": chain, "planet_type_id": pt,
                    "planet_type": C.PLANET_TYPES[pt] if chain in EXTRACTION_CHAINS else None,
                    "radius_km": radius,
                    "factories": ev.design.total_factories, "heads": ev.design.total_heads,
                    "launchpads": ev.design.launchpads, "storages": ev.design.storages,
                    "output_per_day": e.output_units_per_day,
                    "profit_per_day": e.profit_per_day,
                    "revenue_per_day": e.revenue_per_day,
                    "costs_per_day": {
                        "inputs": e.input_cost_per_day, "export_tax": e.export_tax_per_day,
                        "import_tax": e.import_tax_per_day, "freight": e.freight_per_day,
                        "setup": e.setup_per_day,
                    },
                    "worth_it": e.worth_it, "reason": e.reason,
                    "interactions_per_week": e.interactions_per_week,
                    "isk_per_interaction": e.isk_per_interaction,
                    "isk_per_m3": e.isk_per_m3,
                    "haul_m3_per_week": e.haul_m3_per_week,
                    "market_share": e.market_share,
                    "trend": prices.trend.get(product),
                    "buffer_hours": None if math.isinf(ev.buffer_hours) else ev.buffer_hours,
                })
    rows.sort(key=lambda r: -r["profit_per_day"])
    return rows


def do_profitability(zone: Optional[str] = None, cc_level: Optional[int] = None,
                     cfg: PiConfig = PI_CONFIG) -> dict:
    static = _static()
    z = _zone_or_default(zone, cfg)
    lv = _cc_level(cc_level, cfg)
    prices = _prices(static, cfg)
    rows = _profit_rows(static, cfg, z, lv, prices)
    return {"zone": z, "cc_level": lv, "assumptions": _assumptions_dict(cfg, z), "rows": rows}


def _assumptions_dict(cfg: PiConfig, zone: str) -> dict:
    a = _assumptions(cfg, zone)
    m = _market(cfg, zone, assumptions=a)
    return {
        "zone": zone, "yield_per_head": a.yield_per_head, "effective_yield_per_head": a.effective_yield,
        "program_hours": a.program_hours, "interval_hours": a.interval_hours,
        "tax_rate": m.tax_rate, "freight_per_m3": m.freight_per_m3, "valuation": m.valuation,
        "hub_region_id": int(cfg.hub_region_id),
    }


# ------------------------------------------------------------------ planner
def _design_from_input(static: StaticData, planet: Planet, chain: str, product: int, cc_level: int,
                       design: dict) -> Design:
    try:
        d = Design.from_dict({**design, "chain": chain, "product_type_id": product,
                              "planet_type_id": planet.planet_type_id, "cc_level": cc_level})
    except (KeyError, TypeError, ValueError) as e:
        raise ActionError(f"Invalid design: {e}") from e
    if not (1 <= d.launchpads <= 10) or not (0 <= d.storages <= 10):
        raise ActionError("Launchpads must be 1-10, storage facilities 0-10")
    if any(n < 0 or n > 100 for _t, n in d.factories) or any(not (1 <= h <= 10) for _p, h in d.ecus):
        raise ActionError("Factory counts must be 0-100 and heads 1-10 per extractor")
    if len(d.ecus) > 6:
        raise ActionError("At most 6 extractors")
    made = set(engine.made_types(static, product, CHAINS[chain][0]))
    unknown = [t for t, _n in d.factories if t not in made]
    if unknown:
        raise ActionError("Factories for products this chain does not make: "
                          + ", ".join(static.name(t) for t in unknown))
    return d


def do_planner(chain: str, product_type_id: int, planet_id: Optional[int] = None,
               planet_type_id: Optional[int] = None, radius_km: Optional[float] = None,
               cc_level: Optional[int] = None, zone: Optional[str] = None,
               owner_tax_rate: Optional[float] = None, freight_per_m3: Optional[float] = None,
               yield_per_head: Optional[float] = None, program_hours: Optional[float] = None,
               interval_hours: Optional[float] = None, design: Optional[dict] = None,
               cfg: PiConfig = PI_CONFIG) -> dict:
    """Best design (or the given design) for one planet, with economics."""
    static = _static()
    if chain not in CHAINS:
        raise ActionError(f"Unknown chain {chain!r}")
    planet, planet_zone, system = _planet_from(static, planet_id, planet_type_id, radius_km)
    z = _zone_or_default(zone or planet_zone, cfg)
    lv = _cc_level(cc_level, cfg)
    reason = engine.check_feasible(static, planet.planet_type_id, chain, int(product_type_id))
    if reason:
        raise ActionError(reason)
    a = _assumptions(cfg, z, yield_per_head, program_hours, interval_hours)
    if design:
        d = _design_from_input(static, planet, chain, int(product_type_id), lv, design)
        ev = engine.evaluate(static, planet, d, a)
    else:
        ev, why = engine.best_design(static, planet, chain, int(product_type_id), lv, a, exact_radius=True)
        if ev is None:
            raise ActionError(why or "Nothing fits")
    m = _market(cfg, z, owner_tax_rate, freight_per_m3, assumptions=a)
    prices = _prices(static, cfg)
    e = econ.compute(ev, static, prices, m)
    return {
        "planet": {"planet_id": planet.planet_id, "name": planet.name, "planet_type_id": planet.planet_type_id,
                   "planet_type": C.PLANET_TYPES[planet.planet_type_id], "radius_km": planet.radius_km,
                   "system": system},
        "zone": z, "cc_level": lv, "chain": chain,
        "product": {"type_id": int(product_type_id), "name": static.name(int(product_type_id))},
        "assumptions": {"yield_per_head": a.yield_per_head, "effective_yield_per_head": a.effective_yield,
                        "program_hours": a.program_hours, "interval_hours": a.interval_hours,
                        "tax_rate": m.tax_rate, "freight_per_m3": m.freight_per_m3},
        "evaluation": evaluation_dict(static, ev),
        "economics": _economics_dict(e, static),
        "prices": {t["type_id"]: {"sell": prices.sell.get(t["type_id"]), "buy": prices.buy.get(t["type_id"])}
                   for t in _named_rates(static, {**ev.imports, **ev.exports})},
        "trend": prices.trend.get(int(product_type_id)),
    }


# ------------------------------------------------------------------ chains
def do_chain(product_type_id: int, zone: Optional[str] = None, cc_level: Optional[int] = None,
             cfg: PiConfig = PI_CONFIG) -> dict:
    static = _static()
    product = int(product_type_id)
    if product not in static.commodities or static.tier(product) == 0:
        raise ActionError("Choose a P1-P4 product")
    z = _zone_or_default(zone, cfg)
    lv = _cc_level(cc_level, cfg)
    a = _assumptions(cfg, z)
    m = _market(cfg, z, assumptions=a)
    prices = _prices(static, cfg)
    medians = _radius_medians()

    def best(chain: str, p: int):
        for pt in (2016, 11, 13, 12, 2014, 2015, 2017, 2063):
            if engine.check_feasible(static, pt, chain, p) is None:
                return engine.best_design(static, Planet(pt, cfg.pi_reference_radius_km), chain, p, lv, a)
        return None, "No planet type can build this stage"

    def extract(p1: int):
        found = None
        for pt in C.PLANET_TYPE_IDS:
            if engine.check_feasible(static, pt, "P0-P1", p1) is not None:
                continue
            ev, _why = engine.best_design(static, Planet(pt, medians.get(pt, 5000.0)), "P0-P1", p1, lv, a)
            if ev and (found is None or ev.effective_product_per_hour > found.effective_product_per_hour):
                found = ev
        return (found, None) if found else (None, "No planet type carries the P0")

    result = chains_mod.chain_plan(static, product, best, extract, prices, m)
    result["zone"] = z
    result["cc_level"] = lv
    return result


# ------------------------------------------------------------------ systems & planets
def do_search_systems(query: str) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    return [
        {"solar_system_id": int(r[0]), "name": r[1], "security": r[2], "region_id": r[3],
         "zone": econ.security_zone(r[2], r[3]), "planet_count": int(r[4])}
        for r in storage.search_pi_systems(q[:50])
    ]


def do_system_planets(solar_system_id: int) -> dict:
    system = storage.get_solar_system(int(solar_system_id))
    if system is None:
        raise ActionError(f"Unknown solar system {solar_system_id}")
    planets = storage.pi_planets_in_system(int(solar_system_id))
    return {
        "solar_system_id": int(system[0]), "name": system[1], "security": system[2], "region_id": system[3],
        "zone": econ.security_zone(system[2], system[3]),
        "reachable": system[3] not in C.UNREACHABLE_REGION_IDS,
        "planets": [{"planet_id": int(p[0]), "name": p[1], "planet_type_id": int(p[2]),
                     "planet_type": C.PLANET_TYPES.get(int(p[2])), "radius_km": float(p[3])} for p in planets],
    }


# ------------------------------------------------------------------ plans (D6)
def do_list_plans() -> list[dict]:
    return storage.list_pi_plans()


def do_save_plan(plan: dict, plan_id: Optional[int] = None) -> dict:
    name = str(plan.get("name") or "").strip()
    if not name or len(name) > 100:
        raise ActionError("A plan needs a name (at most 100 characters)")
    design = plan.get("design")
    if not isinstance(design, dict) or design.get("chain") not in CHAINS:
        raise ActionError("A plan needs a design with a valid chain")
    try:
        Design.from_dict(design)
    except (KeyError, TypeError, ValueError) as e:
        raise ActionError(f"Invalid design: {e}") from e
    planet_type_id = int(plan.get("planet_type_id") or design.get("planet_type_id") or 0)
    if planet_type_id not in C.PLANET_TYPE_IDS:
        raise ActionError("Unknown planet type")
    for key, lo, hi in (("owner_tax_rate", 0, 1), ("freight_per_m3", 0, 1e9), ("yield_override", 0, 1e7)):
        v = plan.get(key)
        if v is not None and not (lo <= float(v) <= hi):
            raise ActionError(f"{key} out of range")
    row = {
        "name": name, "planet_id": plan.get("planet_id"), "planet_type_id": planet_type_id,
        "radius_km": float(plan.get("radius_km") or 0) or 5000.0,
        "character_id": plan.get("character_id"), "design": design,
        "owner_tax_rate": plan.get("owner_tax_rate"), "freight_per_m3": plan.get("freight_per_m3"),
        "yield_override": plan.get("yield_override"), "notes": (plan.get("notes") or None),
    }
    try:
        new_id = storage.save_pi_plan(row, plan_id)
    except KeyError as e:
        raise ActionError(f"Unknown plan {plan_id}") from e
    return storage.get_pi_plan(new_id) or {}


def do_delete_plan(plan_id: int) -> dict:
    if not storage.delete_pi_plan(int(plan_id)):
        raise ActionError(f"Unknown plan {plan_id}")
    return {"deleted": int(plan_id)}


# ------------------------------------------------------------------ settings
def do_get_settings(cfg: PiConfig = PI_CONFIG) -> dict:
    from dataclasses import fields

    return {f.name: getattr(cfg, f.name) for f in fields(PiConfig)}


def do_update_settings(updates: dict[str, Any], cfg: PiConfig = PI_CONFIG) -> dict:
    from dataclasses import fields

    known = {f.name for f in fields(PiConfig)}
    unknown = sorted(set(updates) - known)
    if unknown:
        raise ActionError("Unknown PI setting(s): " + ", ".join(unknown))
    try:
        validate_pi_overrides(updates)
        save_tenant_config_overrides("pi", updates, cfg, cfg_type=PiConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    engine.clear_cache()
    return do_get_settings(cfg)

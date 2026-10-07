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
from datetime import datetime, timezone
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
    if yield_override is not None:
        y = float(yield_override)
    else:
        calibrated = _calibrated_yield_for_zone(zone)
        y = cfg.yield_for_zone(zone) if calibrated is None else calibrated
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


def _structure_fallback_slug(cfg: PiConfig) -> Optional[str]:
    """Goonmetrics market for the PI price structure: PI's own setting, else
    the slug Trading (structure_market_slug) or Production (home_market)
    already use for that same structure."""
    return cfg.pi_price_structure_slug or _known_structure_slug(int(cfg.pi_price_structure_id or 0))


def _known_structure_slug(sid: int) -> Optional[str]:
    from ..config import TRADING_CONFIG
    from ..production.config import PRODUCTION_CONFIG

    if TRADING_CONFIG.structure_id == sid and TRADING_CONFIG.structure_market_slug:
        return TRADING_CONFIG.structure_market_slug
    if PRODUCTION_CONFIG.home_location_id == sid and PRODUCTION_CONFIG.home_market:
        return PRODUCTION_CONFIG.home_market
    return None


def market_label(cfg: PiConfig) -> str:
    sid = int(cfg.pi_price_structure_id or 0)
    if sid:
        try:
            name = storage.get_location_names([sid]).get(sid)
        except Exception:  # noqa: BLE001 - names are cosmetic
            name = None
        slug = _structure_fallback_slug(cfg)
        return name or (f"{slug} (structure)" if slug else f"structure {sid}")
    from ..hubs import hub_name

    return "the best hub per item" if int(cfg.hub_region_id) == ALL_HUBS else hub_name(int(cfg.hub_region_id))


def _structure_stats(ids: list[int], cfg: PiConfig) -> tuple[dict, bool]:
    """(stats per type, used_goonmetrics_fallback) for the PI price structure."""
    from ..actions import structure_book_auth_roles
    from ..auth import TokenManager
    from ..config import OAUTH_CONFIG
    from ..esi_client import ESIClient, ESIError

    client = ESIClient(tokens=TokenManager(OAUTH_CONFIG))
    try:
        return client.structure_order_stats_bulk_or_goonmetrics(
            int(cfg.pi_price_structure_id), ids, structure_book_auth_roles(), _structure_fallback_slug(cfg))
    except ESIError as e:
        raise ActionError(f"Structure market prices unavailable: {e}") from e


def _prices(static: StaticData, cfg: PiConfig, with_history: bool = True) -> econ.Prices:
    """Order-book prices for every PI commodity at the PI hub (or the best
    hub per item, or a player structure such as C-J), with PI's own freight
    rate (P-34)."""
    from ..esi_client import ESIClient

    ids = sorted(static.commodities)
    volumes = {t: c.volume for t, c in static.commodities.items()}
    if int(cfg.pi_price_structure_id or 0) > 0:
        stats, fallback = _structure_stats(ids, cfg)
        sell = {t: (stats[t].sell_percentile if t in stats else None) for t in ids}
        buy = {t: (stats[t].buy_percentile if t in stats else None) for t in ids}
        trends: dict[int, Optional[dict]] = {}
        if with_history:
            # Structures have no market history; the region the structure
            # sits in is the closest signal (Trading's reference region).
            from ..config import TRADING_CONFIG

            trends = _trends(static, int(TRADING_CONFIG.reference_region_id))
        daily = {t: (tr or {}).get("avg_daily_volume") for t, tr in trends.items()}
        return econ.Prices(sell=sell, buy=buy, hub_by_type={}, daily_volume=daily, trend=trends,
                           source_note="Goonmetrics snapshot (no character could read the structure market)"
                           if fallback else None)
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
    d["unpriced_surplus"] = [{"type_id": t, "name": static.name(t)} for t in e.unpriced_surplus]
    return d


# ------------------------------------------------------------------ meta
def price_structures(cfg: PiConfig) -> list[dict]:
    """Player structures offered as a PI market: the home structures Trading
    (structure_id) and Production (home_location_id) already use - C-J - plus
    whatever PI is set to now."""
    from ..config import TRADING_CONFIG
    from ..production.config import PRODUCTION_CONFIG

    ids = [i for i in (TRADING_CONFIG.structure_id, PRODUCTION_CONFIG.home_location_id,
                       int(cfg.pi_price_structure_id or 0)) if i]
    ids = list(dict.fromkeys(int(i) for i in ids))
    try:
        names = storage.get_location_names(ids) if ids else {}
    except Exception:  # noqa: BLE001 - names are cosmetic
        names = {}
    out = []
    for i in ids:
        slug = _known_structure_slug(i)
        out.append({"structure_id": i, "name": names.get(i) or (f"{slug} (structure)" if slug else f"Structure {i}")})
    return out



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
        "price_structures": price_structures(cfg),
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
    return {"zone": z, "cc_level": lv, "assumptions": {**_assumptions_dict(cfg, z), "price_note": prices.source_note},
            "rows": rows}


def _assumptions_dict(cfg: PiConfig, zone: str) -> dict:
    a = _assumptions(cfg, zone)
    m = _market(cfg, zone, assumptions=a)
    return {
        "zone": zone, "yield_per_head": a.yield_per_head, "effective_yield_per_head": a.effective_yield,
        "program_hours": a.program_hours, "interval_hours": a.interval_hours,
        "tax_rate": m.tax_rate, "freight_per_m3": m.freight_per_m3, "valuation": m.valuation,
        "hub_region_id": int(cfg.hub_region_id),
        "price_structure_id": int(cfg.pi_price_structure_id or 0),
        "market_label": market_label(cfg),
        **_tax_parts(cfg, zone),
    }


def _tax_parts(cfg: PiConfig, zone: str, owner_tax_rate: Optional[float] = None) -> dict:
    """The customs rate split into its NPC part (high-sec only) and the
    owner part, so the UI can show why a rate is what it is."""
    owner = cfg.pi_owner_tax_rate if owner_tax_rate is None else float(owner_tax_rate)
    return {"npc_tax_rate": econ.npc_tax_rate(zone, cfg.pi_customs_code_expertise_level),
            "owner_tax_rate": owner, "default_zone": cfg.pi_zone}


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
                        "tax_rate": m.tax_rate, "freight_per_m3": m.freight_per_m3,
                        "market_label": market_label(cfg), **_tax_parts(cfg, z, owner_tax_rate)},
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


# ------------------------------------------------------------------ characters (D3)
def do_characters(cfg: PiConfig = PI_CONFIG) -> dict:
    from . import skills

    return skills.load_overview(cfg)


# ------------------------------------------------------------------ system analysis (3.5)
_FACTORY_PLANET_TYPE_ORDER = (2016, 11, 13, 12, 2014, 2015, 2017, 2063)
SYSTEM_PLAN_TIME_LIMIT_S = 2.0
FACTORY_PLANETS_PER_SYSTEM = 3


def do_system_analysis(solar_system_id: int, slots: Optional[int] = None, characters: Optional[int] = None,
                       cc_level: Optional[int] = None, owner_tax_rate: Optional[float] = None,
                       cfg: PiConfig = PI_CONFIG) -> dict:
    """What is worth building in one system (PI_PLAN 3.5): per-planet best
    uses and the best colony combination for the player's slots, letting
    colonies in the system feed each other."""
    from . import system_plan as sp

    static = _static()
    system = do_system_planets(solar_system_id)
    if not system["planets"]:
        raise ActionError("This system has no PI planets")
    zone = system["zone"]
    overview: dict = {}
    if slots is None or characters is None or cc_level is None:
        try:
            overview = do_characters(cfg)
        except Exception:  # noqa: BLE001 - token store unavailable -> manual values
            overview = {}
    n_slots = int(slots if slots is not None else
                  overview.get("total_slots") or cfg.pi_characters * cfg.pi_planets_per_character)
    n_chars = int(characters if characters is not None else overview.get("character_count") or cfg.pi_characters)
    lv = _cc_level(cc_level if cc_level is not None else overview.get("max_cc_level"), cfg)
    if not (0 < n_slots <= 60) or not (0 < n_chars <= 100):
        raise ActionError("Slots must be 1-60 and characters 1-100")
    a = _assumptions(cfg, zone)
    m = _market(cfg, zone, owner_tax_rate, assumptions=a)
    prices = _prices(static, cfg)

    planets = [Planet(p["planet_type_id"], p["radius_km"], p["planet_id"], p["name"], int(solar_system_id))
               for p in system["planets"]]
    # Factory colonies can go on any planet of the system, and the smallest
    # planets have the cheapest links - so factory options are evaluated on
    # the few smallest planets only (plus the smallest Barren/Temperate ones
    # for P4). Keeps the number of design searches bounded.
    by_size = sorted(planets, key=lambda p: p.radius_km)
    factory_planets = by_size[:FACTORY_PLANETS_PER_SYSTEM]
    p4_planets = [p for p in by_size if static.has_kind(C.KIND_HIGH_TECH, p.planet_type_id)][:2]

    options: list = []
    per_planet: dict[int, list[dict]] = {p.planet_id: [] for p in planets}
    cannot: list[str] = []
    if not p4_planets:
        cannot.append("No Barren or Temperate planet: no P4 (High-Tech Production Plants) in this system.")
    for planet in planets:
        candidates = [(ch, prod) for ch in EXTRACTION_CHAINS for prod in static.products_of_tier(CHAINS[ch][1])]
        if planet in factory_planets:
            candidates += [(ch, prod) for ch, (_s, t) in CHAINS.items() if ch not in EXTRACTION_CHAINS and t < 4
                           for prod in static.products_of_tier(t)]
        if planet in p4_planets:
            candidates += [(ch, prod) for ch, (_s, t) in CHAINS.items() if t == 4 for prod in static.products_of_tier(4)]
        for chain, product in candidates:
            if engine.check_feasible(static, planet.planet_type_id, chain, product) is not None:
                continue
            ev, _why = engine.best_design(static, planet, chain, product, lv, a)
            if ev is None or ev.effective_product_per_hour <= 0:
                continue
            e = econ.compute(ev, static, prices, m)
            f = 24.0 * ev.effective_factor
            taxes = e.export_tax_per_day + e.import_tax_per_day
            options.append(sp.Option(
                key=f"{planet.planet_id}:{chain}:{product}", planet_id=planet.planet_id, chain=chain,
                product_type_id=product, is_extraction=chain in EXTRACTION_CHAINS,
                exports={t: q * f for t, q in ev.exports.items()},
                imports={t: q * f for t, q in ev.imports.items()},
                fixed_cost_per_day=taxes + e.setup_per_day, setup_cost_per_day=e.setup_per_day,
                label=f"{static.name(product)} ({chain})",
            ))
            per_planet[planet.planet_id].append({
                "chain": chain, "product_type_id": product, "product_name": static.name(product),
                "profit_per_day": e.profit_per_day, "worth_it": e.worth_it, "reason": e.reason,
                "output_per_day": e.output_units_per_day, "design": ev.design.to_dict(),
            })

    sell = {t: econ.output_unit_value(t, prices, m) for t in static.commodities}
    buy = {t: econ.input_unit_cost(t, prices, m) for t in static.commodities}
    market = sp.Market(
        sell_value={t: v for t, v in sell.items() if v is not None},
        buy_cost={t: v for t, v in buy.items() if v is not None},
        freight_per_unit={t: c.volume * m.freight_per_m3 for t, c in static.commodities.items()},
        max_sell_per_day={t: v * m.market_share_warning for t, v in prices.daily_volume.items() if v},
    )
    plan = sp.plan_system(options, market, n_slots, n_chars, time_limit_s=SYSTEM_PLAN_TIME_LIMIT_S)
    for rows in per_planet.values():
        rows.sort(key=lambda r: -r["profit_per_day"])
        del rows[8:]
    names = {p.planet_id: p.name for p in planets}
    return {
        "system": {k: v for k, v in system.items() if k != "planets"},
        "zone": zone, "slots": n_slots, "characters": n_chars, "cc_level": lv,
        "assumptions": {"yield_per_head": a.yield_per_head, "program_hours": a.program_hours,
                        "interval_hours": a.interval_hours, "tax_rate": m.tax_rate},
        "planets": [{**p, "best": per_planet.get(p["planet_id"], [])} for p in system["planets"]],
        "plan": {
            "status": plan.status,
            "profit_per_day": plan.profit_per_day,
            "profit_per_slot": plan.profit_per_slot,
            "used_slots": plan.used_slots,
            "best_single_uses_profit_per_day": plan.best_single_uses_profit_per_day,
            "chain_gain_per_day": plan.chain_gain_per_day,
            "colonies": [{"planet_id": c.planet_id, "planet_name": names.get(c.planet_id), "chain": c.chain,
                          "product_type_id": c.product_type_id, "product_name": static.name(c.product_type_id),
                          "count": c.count, "yield_factors": list(c.yield_factors)} for c in plan.colonies],
            "flows": [{"type_id": fl.type_id, "name": static.name(fl.type_id), "produced": fl.produced,
                       "consumed": fl.consumed, "internal": fl.internal, "sold": fl.sold, "bought": fl.bought}
                      for fl in plan.flows],
            "notes": list(plan.notes),
        },
        "cannot": cannot + ([] if system["reachable"] else ["This region is not reachable (Jove space)."]),
    }


# ------------------------------------------------------------------ production demand (F1)
def do_production_demand(cfg: PiConfig = PI_CONFIG) -> dict:
    """PI commodities on Production's latest buy list: make via PI vs buy.
    The router additionally requires the `production` grant (P-40)."""
    from . import demand

    static = _static()
    buy_list = storage.load_latest_buy_list()
    z = cfg.pi_zone
    lv = cfg.pi_cc_level
    a = _assumptions(cfg, z)
    m = _market(cfg, z, assumptions=a)
    pi_items = {t: q for t, q in buy_list.items() if t in static.commodities and static.tier(t)}
    if not pi_items:
        return {"rows": [], "days": cfg.pi_demand_days, "zone": z}
    prices = _prices(static, cfg, with_history=False)
    medians = _radius_medians()

    def candidates(chain: str, product: int) -> list[Evaluation]:
        out = []
        if chain in EXTRACTION_CHAINS:
            for pt in C.PLANET_TYPE_IDS:
                if engine.check_feasible(static, pt, chain, product) is None:
                    ev, _ = engine.best_design(static, Planet(pt, medians.get(pt, 5000.0)), chain, product, lv, a)
                    if ev:
                        out.append(ev)
        else:
            for pt in _FACTORY_PLANET_TYPE_ORDER:
                if engine.check_feasible(static, pt, chain, product) is None:
                    ev, _ = engine.best_design(static, Planet(pt, cfg.pi_reference_radius_km), chain, product, lv, a)
                    if ev:
                        out.append(ev)
                    break
        return out

    rows = demand.demand_rows(static, pi_items, prices, m, candidates, cfg.pi_demand_days)
    return {"rows": rows, "days": cfg.pi_demand_days, "zone": z}


# ------------------------------------------------------------------ templates & layouts (6, 6A)
def analysis_dict(static: StaticData, a) -> dict:
    return {
        "ok": a.ok,
        "findings": [f.to_dict() for f in a.findings],
        "kinds": a.kinds,
        "cpu_used": a.cpu_used, "cpu_capacity": a.cpu_capacity,
        "power_used": a.power_used, "power_capacity": a.power_capacity,
        "link_cpu": a.link_cpu, "link_power": a.link_power,
        "links": [{"km": km, "load_m3h": load, "capacity_m3h": cap}
                  for km, load, cap in zip(a.link_km, a.link_load_m3h, a.link_capacity_m3h)],
        "runs_per_hour": {str(k): v for k, v in a.runs_per_hour.items()},
        "max_runs_per_hour": {str(k): v for k, v in a.max_runs_per_hour.items()},
        "extracted": _named_rates(static, a.extracted),
        "produced": _named_rates(static, a.produced),
        "imports": _named_rates(static, a.imports),
        "exports": _named_rates(static, a.exports),
        "storage_m3": a.storage_m3, "buffer_hours": a.buffer_hours,
        "import_m3_per_hour": a.import_m3_per_hour, "export_m3_per_hour": a.export_m3_per_hour,
        "product_type_id": a.product_type_id,
        "product_name": static.name(a.product_type_id) if a.product_type_id else None,
        "chain": a.chain, "setup_isk": a.setup_isk,
    }


def _layout_payload(static: StaticData, layout, analysis) -> dict:
    from .layout import template_io

    return {
        "template": template_io.to_dict(layout),
        "template_json": template_io.to_json(layout),
        "analysis": analysis_dict(static, analysis),
    }


def _parse_template(template: Any):
    from .layout import template_io

    try:
        return template_io.parse(template)
    except template_io.TemplateError as e:
        raise ActionError(str(e)) from e


def _radius_for(planet_id: Optional[int], radius_km: Optional[float]) -> Optional[float]:
    if planet_id is not None:
        row = storage.get_pi_planet(int(planet_id))
        if row is None:
            raise ActionError(f"Unknown PI planet {planet_id}")
        return float(row[4])
    return float(radius_km) if radius_km else None


def do_validate_layout(template: Any, planet_id: Optional[int] = None, radius_km: Optional[float] = None,
                       yield_per_head: Optional[float] = None, program_hours: Optional[float] = None,
                       yield_by_pin: Optional[dict] = None, cycle_by_pin: Optional[dict] = None,
                       cfg: PiConfig = PI_CONFIG) -> dict:
    """Analyse any template/layout (pasted, stored, generated or edited).

    `program_hours` sets the extractor cycle the route quantities were written
    for (a template stores quantity per cycle). `yield_by_pin` / `cycle_by_pin`
    are 1-based pin maps for a real colony, where each extractor has its own
    program."""
    from . import decay
    from .layout import validate

    static = _static()
    layout = _parse_template(template)
    y = float(yield_per_head) if yield_per_head else _assumptions(cfg, cfg.pi_zone).effective_yield
    cycle = decay.cycle_seconds_for_program(float(program_hours)) if program_hours else None
    a = validate.analyse(static, layout, _radius_for(planet_id, radius_km), y,
                         yield_by_pin=yield_by_pin, cycle_seconds=cycle, cycle_by_pin=cycle_by_pin)
    return _layout_payload(static, layout, a)


def do_generate_layout(chain: str, product_type_id: int, planet_id: Optional[int] = None,
                       planet_type_id: Optional[int] = None, radius_km: Optional[float] = None,
                       cc_level: Optional[int] = None, zone: Optional[str] = None,
                       design: Optional[dict] = None, yield_per_head: Optional[float] = None,
                       program_hours: Optional[float] = None, interval_hours: Optional[float] = None,
                       shape: Optional[str] = None, comment: Optional[str] = None,
                       use_references: bool = True, cfg: PiConfig = PI_CONFIG) -> dict:
    """Design (best or given) -> importable template (6A). With the standard
    shape, a community reference layout adapted to this product
    (layout/reference.py) replaces the generated one when its effective
    output is at least REFERENCE_MIN_SHARE of the generator's."""
    from .layout import generate

    static = _static()
    if chain not in CHAINS:
        raise ActionError(f"Unknown chain {chain!r}")
    planet, planet_zone, _system = _planet_from(static, planet_id, planet_type_id, radius_km)
    z = _zone_or_default(zone or planet_zone, cfg)
    lv = _cc_level(cc_level, cfg)
    reason = engine.check_feasible(static, planet.planet_type_id, chain, int(product_type_id))
    if reason:
        raise ActionError(reason)
    a = _assumptions(cfg, z, yield_per_head, program_hours, interval_hours)
    if design:
        d = _design_from_input(static, planet, chain, int(product_type_id), lv, design)
    else:
        ev, why = engine.best_design(static, planet, chain, int(product_type_id), lv, a, exact_radius=True)
        if ev is None:
            raise ActionError(why or "Nothing fits")
        d = ev.design
    cells_fn = generate.standard_cells
    if shape and shape != "standard":
        from .layout import shapes

        try:
            cells_fn = shapes.provider(shape)
        except KeyError as e:
            raise ActionError(f"Unknown shape {shape!r}") from e
    shape_note: Optional[str] = None
    standard = cells_fn is generate.standard_cells
    from . import decay

    cycle = decay.cycle_seconds_for_program(a.program_hours)
    try:
        res = generate.generate(static, d, planet.planet_type_id, planet.radius_km, a.effective_yield,
                                comment or "", cells_fn, shrink_to_fit=standard, cycle_seconds=cycle)
    except generate.GenerateError as e:
        if standard:
            ref = _reference_layout(static, d, planet, a, comment, None) if use_references else None
            if ref is None:
                raise ActionError(str(e)) from e
            return _generated_payload(static, ref.layout, ref.analysis, ref.design, planet,
                                      [_reference_note(ref)], "standard", ref)
        # A shape is never applied half-way: standard layout plus the reason.
        shape_note = f"The {shape} shape does not work for this colony ({e}); the standard layout is shown."
        try:
            res = generate.generate(static, d, planet.planet_type_id, planet.radius_km, a.effective_yield,
                                    comment or "", cycle_seconds=cycle)
        except generate.GenerateError as e2:
            raise ActionError(str(e2)) from e2
    if standard and use_references:
        ref = _reference_layout(static, d, planet, a, comment, res)
        if ref is not None:
            return _generated_payload(static, ref.layout, ref.analysis, ref.design, planet,
                                      [_reference_note(ref)], "standard", ref)
    return _generated_payload(static, res.layout, res.analysis, res.design, planet,
                              res.notes + ([shape_note] if shape_note else []),
                              shape if not shape_note else "standard", None)


# A reference layout wins over the generator from this share of its
# effective output on: proven in game, so a near tie goes to the reference.
REFERENCE_MIN_SHARE = 0.98


def _reference_layout(static, design, planet, a, comment, generated):
    """The best adapted reference layout, or None when none fits or the
    generated layout (`generated`, may be None) does clearly better."""
    from . import decay
    from .layout import reference, validate

    cycle = decay.cycle_seconds_for_program(a.program_hours)
    ref = reference.from_references(static, design, planet.planet_type_id, planet.radius_km,
                                    a.effective_yield, a.interval_hours, comment or "", cycle_seconds=cycle)
    if ref is None or generated is None:
        return ref
    own = validate.analyse(static, generated.layout, planet.radius_km, a.effective_yield,
                           reference.self_supplied(static, generated.design), cycle_seconds=cycle)
    own_out = reference.effective_output(own, design.product_type_id, a.interval_hours)
    return ref if ref.effective_output >= own_out * REFERENCE_MIN_SHARE else None


def _reference_note(ref) -> str:
    return (f"Based on a community layout ({ref.reference.upvotes} upvotes on planetsin.space), adapted to this "
            "product and planet.")


def _generated_payload(static, layout, analysis, design, planet, notes, shape, ref) -> dict:
    payload = _layout_payload(static, layout, analysis)
    payload.update({"design": design.to_dict(), "notes": notes, "shape": shape,
                    "source": "reference" if ref is not None else "generator",
                    "planet": {"planet_id": planet.planet_id, "planet_type_id": planet.planet_type_id,
                               "radius_km": planet.radius_km}})
    return payload


def do_list_templates() -> list[dict]:
    return storage.list_pi_templates()


def do_get_template(template_id: int, planet_id: Optional[int] = None, radius_km: Optional[float] = None,
                    cfg: PiConfig = PI_CONFIG) -> dict:
    row = storage.get_pi_template(int(template_id))
    if row is None:
        raise ActionError(f"Unknown template {template_id}")
    fallback_radius = (row.get("diameter_km") or 0) / 2 or None
    payload = do_validate_layout(row["template"], planet_id, radius_km or fallback_radius, cfg=cfg)
    return {**{k: v for k, v in row.items() if k != "template"}, **payload}


def do_save_template(template: Any, name: Optional[str] = None, source: str = "paste",
                     template_id: Optional[int] = None) -> dict:
    from .layout import template_io

    layout = _parse_template(template)
    if source not in ("paste", "generated", "esi"):
        raise ActionError("Unknown template source")
    label = (name or layout.comment or "Template").strip()[:100]
    try:
        new_id = storage.save_pi_template(label, layout.comment or None, layout.planet_type_id, layout.cc_level,
                                          layout.diameter_km, template_io.to_dict(layout), source, template_id)
    except KeyError as e:
        raise ActionError(f"Unknown template {template_id}") from e
    return {k: v for k, v in (storage.get_pi_template(new_id) or {}).items() if k != "template"}


def do_delete_template(template_id: int) -> dict:
    if not storage.delete_pi_template(int(template_id)):
        raise ActionError(f"Unknown template {template_id}")
    return {"deleted": int(template_id)}


def do_export_template(template_id: int, pretty: bool = False) -> dict:
    from .layout import template_io

    row = storage.get_pi_template(int(template_id))
    if row is None:
        raise ActionError(f"Unknown template {template_id}")
    layout = _parse_template(row["template"])
    return {"name": row["name"], "json": template_io.to_json(layout, pretty=pretty)}


def do_retarget_template(template: Any, planet_type_id: Optional[int] = None,
                         product_type_id: Optional[int] = None, planet_id: Optional[int] = None,
                         radius_km: Optional[float] = None, cfg: PiConfig = PI_CONFIG) -> dict:
    """Same geometry for another planet type (structure ids rewritten) and/or
    another product of the same tier and input count (factory schematics and
    routed commodities rewritten). Re-validated afterwards (PI_PLAN 6.4)."""
    from .layout import retarget, validate

    static = _static()
    layout = _parse_template(template)
    try:
        new_layout = retarget.retarget(static, layout, planet_type_id, product_type_id)
    except retarget.RetargetError as e:
        raise ActionError(str(e)) from e
    y = _assumptions(cfg, cfg.pi_zone).effective_yield
    a = validate.analyse(static, new_layout, _radius_for(planet_id, radius_km), y)
    return _layout_payload(static, new_layout, a)


# ------------------------------------------------------------------ calibration (D2, P-54)
MIN_CALIBRATION_SAMPLES = 3
_CALIBRATION_TTL = 600.0
# {tenant_id: (cached_at, {zone: calibrated yield})} - per tenant, since the
# samples are the tenant's own. Cleared whenever samples are written.
_calibration_cache: dict[str, tuple[float, dict[str, float]]] = {}
_calibration_lock = threading.Lock()
_planet_zone_cache: dict[int, Optional[str]] = {}   # SDE data, tenant independent


def clear_calibration_cache() -> None:
    with _calibration_lock:
        _calibration_cache.clear()


def _planet_zone(planet_id: int) -> Optional[str]:
    if planet_id in _planet_zone_cache:
        return _planet_zone_cache[planet_id]
    zone = None
    row = storage.get_pi_planet(int(planet_id))
    if row is not None:
        system = storage.get_solar_system(row[2])
        if system is not None:
            zone = econ.security_zone(system[2], system[3])
    _planet_zone_cache[planet_id] = zone
    return zone


def _sample_zone(sample: dict) -> Optional[str]:
    zone = _planet_zone(sample["planet_id"])
    if zone is None and sample.get("security") is not None:
        zone = econ.security_zone(sample["security"], None)
    return zone


def _samples_by_zone(samples: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for smp in samples:
        zone = _sample_zone(smp)
        if zone:
            out.setdefault(zone, []).append(smp)
    return out


def _calibrated_yield_for_zone(zone: str) -> Optional[float]:
    """Median per-head yield (scaled to the reference program) of this
    tenant's real extractor programs in `zone`, once there are at least
    MIN_CALIBRATION_SAMPLES of them; None otherwise - and None whenever
    storage is unavailable, so pure callers never depend on a database."""
    from . import colonies

    try:
        tenant = storage.get_current_tenant()
        if not tenant:
            return None
        tenant = str(tenant)
        with _calibration_lock:
            hit = _calibration_cache.get(tenant)
        if hit and time.time() - hit[0] < _CALIBRATION_TTL:
            return hit[1].get(zone)
        yields = {}
        for z, smps in _samples_by_zone(storage.list_pi_yield_samples()).items():
            y = colonies.calibrated_yield(smps, min_samples=MIN_CALIBRATION_SAMPLES)
            if y is not None:
                yields[z] = y
        with _calibration_lock:
            _calibration_cache[tenant] = (time.time(), yields)
        return yields.get(zone)
    except Exception:  # noqa: BLE001 - calibration is an optional refinement
        log.debug("PI calibration unavailable", exc_info=True)
        return None


def do_calibration(cfg: PiConfig = PI_CONFIG) -> dict:
    """Real-extractor samples per zone and per P0 (per head per hour, scaled
    to the 72 h reference program) and which zones now replace the D2
    default yield."""
    from . import colonies

    samples = storage.list_pi_yield_samples()
    try:
        names = _static()
    except ActionError:
        names = None

    def median(smps: list[dict]) -> Optional[float]:
        return colonies.calibrated_yield(smps, min_samples=1)

    zones = []
    by_zone = _samples_by_zone(samples)
    for z in C.ZONES:
        smps = by_zone.get(z, [])
        zones.append({
            "zone": z, "count": len(smps), "median": median(smps) if smps else None,
            "default": cfg.yield_for_zone(z), "active": len(smps) >= MIN_CALIBRATION_SAMPLES,
        })
    by_p0: dict[int, list[dict]] = {}
    for smp in samples:
        by_p0.setdefault(smp["p0_type_id"], []).append(smp)
    p0 = [{"type_id": t, "name": names.name(t) if names else None, "count": len(v), "median": median(v)}
          for t, v in sorted(by_p0.items())]
    return {"min_samples": MIN_CALIBRATION_SAMPLES, "sample_count": len(samples), "zones": zones, "p0": p0}


# ------------------------------------------------------------------ colonies from ESI (phase 4)
def colony_views(static: StaticData, rows: list[dict], cfg: PiConfig, now: Optional[datetime] = None) -> list[dict]:
    """Stored ESI colony rows (`read_esi("planets", ...)`) -> one view each:
    the converted layout, the projection to `now` and display fields. Pure
    apart from SDE lookups; `colony` (the ColonyLayout) is kept for callers
    that need it and must be dropped before returning JSON."""
    from . import colonies

    now = now or datetime.now(timezone.utc)
    out = []
    for r in rows:
        planet = storage.get_pi_planet(int(r["planet_id"]))
        system_id = int(planet[2]) if planet else r.get("solar_system_id")
        system = storage.get_solar_system(int(system_id)) if system_id else None
        zone = econ.security_zone(system[2], system[3]) if system else econ.security_zone(None, None)
        ptype = colonies.planet_type_id(r.get("planet_type")) or (int(planet[3]) if planet else None)
        radius = float(planet[4]) if planet else None
        detail = {**(r.get("layout") or {}), "upgrade_level": r.get("upgrade_level")}
        colony = colonies.esi_to_layout(static, detail, ptype, radius)
        last_update = colonies.parse_dt(r.get("last_update"))
        y = _assumptions(cfg, zone).effective_yield
        out.append({
            "owner_id": int(r["owner_id"]), "planet_id": int(r["planet_id"]),
            "planet_name": planet[1] if planet else None,
            "planet_type": r.get("planet_type"), "planet_type_id": ptype, "radius_km": radius,
            "solar_system_id": system_id, "system_name": system[1] if system else None,
            "security": system[2] if system else None, "zone": zone,
            "upgrade_level": r.get("upgrade_level"), "last_update": last_update.isoformat() if last_update else None,
            "projection": colonies.project(static, colony, last_update, now, radius, y),
            "template_available": bool(colony.layout.pins),
            "colony": colony,
        })
    return out


def _sample_rows(view: dict) -> list[dict]:
    from . import colonies

    return [
        {**s, "character_id": view["owner_id"], "planet_id": view["planet_id"],
         "planet_type_id": view["planet_type_id"] or 0, "security": view["security"]}
        for s in colonies.calibration_samples(view["colony"])
    ]


def do_colonies(cfg: PiConfig = PI_CONFIG) -> dict:
    """The player's real colonies per character: projected state, extractor
    expiry, and calibration samples from their extractor programs. A
    character is `not_shared` / `reauth_needed` / `not_synced` / `ok`, never
    an error, so one missing share or dead token blanks only that character."""
    from ..auth import TokenManager
    from ..character_management import fields
    from ..config import OAUTH_CONFIG
    from ..esi_data import read_esi

    static = _static()
    tokens = TokenManager(OAUTH_CONFIG)
    characters = fields.token_characters()
    try:
        rows = read_esi("planets", TOOL_KEY, owner_type="character")
    except Exception:  # noqa: BLE001 - no accessor/share -> every character reports its own state
        log.info("PI colonies not readable from ESI", exc_info=True)
        rows = []
    by_char: dict[int, list[dict]] = {}
    for r in rows:
        by_char.setdefault(int(r["owner_id"]), []).append(r)
    now = datetime.now(timezone.utc)
    samples: list[dict] = []
    result = []
    for c in characters:
        cid = c["character_id"]
        blocked = fields.gate("planets", TOOL_KEY, cid, tokens)
        entry: dict = {"character_id": cid, "character_name": c["character_name"],
                       "state": blocked or fields.STATE_OK, "colonies": [], "synced_at": None}
        if not blocked:
            fresh = fields.freshness_by_kind(cid).get("planets")
            entry["synced_at"] = fresh["last_success_at"] if fresh else None
            if entry["synced_at"] is None:
                entry["state"] = fields.STATE_NOT_SYNCED
                entry["detail"] = fresh.get("last_error") if fresh else None
            for v in colony_views(static, by_char.get(cid, []), cfg, now):
                samples.extend(_sample_rows(v))
                entry["colonies"].append({k: val for k, val in v.items() if k != "colony"})
        result.append(entry)
    if samples:
        try:
            storage.upsert_pi_yield_samples(samples)
            clear_calibration_cache()
        except Exception:  # noqa: BLE001 - calibration must never break the monitor
            log.exception("PI yield sample upsert failed")
    return {
        "characters": result,
        "shared": any(e["state"] != fields.STATE_NOT_SHARED for e in result),
        "now": now.isoformat(),
    }


def do_sync_colonies() -> dict:
    """Refresh the `planets` snapshot (and the PI skills) shared with PI."""
    from ..character_management import fields
    from ..esi_data import do_sync_for_tool

    return fields.summarise_sync(do_sync_for_tool(TOOL_KEY))


def do_colony_template(character_id: int, planet_id: int, save: bool = False, name: Optional[str] = None,
                       cfg: PiConfig = PI_CONFIG) -> dict:
    """A real colony as an importable template, validated like any layout;
    with `save` it also goes into the template library (source "esi")."""
    import dataclasses

    from ..esi_data import read_esi
    from . import colonies
    from .layout import template_io

    static = _static()
    try:
        rows = read_esi("planets", TOOL_KEY, owner_type="character", owner_id=int(character_id))
    except Exception as e:  # noqa: BLE001
        raise ActionError("Planetary Industry is not shared with the PI tool for this character") from e
    row = next((r for r in rows if int(r["planet_id"]) == int(planet_id)), None)
    if row is None:
        raise ActionError("Colony not found - share Planetary Industry on the Characters page and sync")
    view = colony_views(static, [row], cfg)[0]
    layout = view["colony"].layout
    if not layout.pins:
        raise ActionError("This colony has no structures to export")
    label = (name or "").strip() or view["planet_name"] or f"Planet {planet_id}"
    layout = dataclasses.replace(layout, comment=template_io.safe_comment(label))
    template = template_io.to_dict(layout)
    y = _assumptions(cfg, view["zone"]).effective_yield
    _rows, yield_by_pin, cycle_by_pin = colonies.extraction_rates(view["colony"], datetime.now(timezone.utc), y)
    payload = do_validate_layout(template, planet_id=int(planet_id) if view["planet_name"] else None,
                                 radius_km=view["radius_km"], yield_by_pin=yield_by_pin or None,
                                 cycle_by_pin=cycle_by_pin or None, cfg=cfg)
    payload["saved"] = do_save_template(template, label, source="esi") if save else None
    payload["skipped_routes"] = view["colony"].skipped_routes
    payload["cc_bypassed"] = view["colony"].cc_bypassed
    return payload

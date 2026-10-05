"""Real colonies from ESI (docs/PI_PLAN.md phase 4, PI_TECHNICAL_DESIGN 6/7) -
pure: ESI colony -> Layout (template), monitor projection, calibration samples.

ESI's planet detail (GET /characters/{id}/planets/{planet_id}) is the state
as of the colony's `last_update` - the last time the player touched it in
game, not a live simulation (P-53). Every "now" figure here is projected
from that state and the colony's rates, and says so.

Open verification (V-3): ESI pin latitude/longitude are assumed to use the
same convention as template La/Lo (polar angle, longitude, radians). Check
one colony against its own in-game export before relying on exported
geometry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import constants as C
from . import decay
from .layout import validate
from .layout.template_io import Layout, Link, Pin, Route, safe_comment
from .model import StaticData

# ESI planet_type strings -> SDE planet type id.
PLANET_TYPE_BY_NAME = {name.lower(): pt for pt, name in C.PLANET_TYPES.items()}

MIN_SAMPLE_PROGRAM_HOURS = 1.0
UNCERTAIN_AFTER_HOURS = 72.0


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def planet_type_id(name: Optional[str]) -> Optional[int]:
    return PLANET_TYPE_BY_NAME.get((name or "").strip().lower())


# ------------------------------------------------------------------ ESI -> layout
@dataclass
class ColonyLayout:
    layout: Layout
    pin_ids: list[int]                          # ESI pin id per layout pin (1-based index - 1)
    contents: dict[int, dict[int, float]]       # layout pin index (1-based) -> {type_id: amount}
    extractors: list[dict] = field(default_factory=list)
    skipped_routes: int = 0


def esi_to_layout(static: StaticData, colony: dict, planet_type: Optional[int], radius_km: Optional[float] = None,
                  comment: str = "") -> ColonyLayout:
    """ESI colony (pins/links/routes as returned by ESI, upgrade_level from
    the planet list) -> Layout. The Command Center is dropped (templates
    never contain it); factory schematic ids become product type ids (P-51);
    route waypoints become the path; quantities are rounded to integers."""
    pins_raw = colony.get("pins") or []
    index: dict[int, int] = {}
    pins: list[Pin] = []
    pin_ids: list[int] = []
    contents: dict[int, dict[int, float]] = {}
    extractors: list[dict] = []
    for p in pins_raw:
        type_id = int(p.get("type_id") or 0)
        spec = static.structures.get(type_id)
        if spec is None or spec.kind == C.KIND_COMMAND_CENTER:
            continue
        product = None
        heads = 0
        ext = p.get("extractor_details") or None
        if spec.kind == C.KIND_ECU and ext:
            product = ext.get("product_type_id")
            heads = len(ext.get("heads") or [])
            extractors.append({
                "pin_id": int(p["pin_id"]), "product_type_id": product, "heads": heads,
                "qty_per_cycle": ext.get("qty_per_cycle"), "cycle_time": ext.get("cycle_time"),
                "install_time": p.get("install_time"), "expiry_time": p.get("expiry_time"),
                "last_cycle_start": p.get("last_cycle_start"),
            })
        elif spec.kind in C.FACTORY_KINDS:
            sid = (p.get("factory_details") or {}).get("schematic_id") or p.get("schematic_id")
            s = static.schematics.get(int(sid)) if sid else None
            product = s.output_type_id if s else None
        pins.append(Pin(type_id=type_id, la=float(p.get("latitude") or 0.0), lo=float(p.get("longitude") or 0.0),
                        product=int(product) if product else None, heads=heads))
        pin_ids.append(int(p["pin_id"]))
        index[int(p["pin_id"])] = len(pins)
        amounts = {}
        for c in p.get("contents") or []:
            amounts[int(c["type_id"])] = amounts.get(int(c["type_id"]), 0.0) + float(c.get("amount") or 0)
        if amounts:
            contents[len(pins)] = amounts
    links = []
    for lk in colony.get("links") or []:
        a, b = index.get(int(lk.get("source_pin_id") or 0)), index.get(int(lk.get("destination_pin_id") or 0))
        if a and b and a != b:
            links.append(Link(a=a, b=b, level=int(lk.get("link_level") or 0)))
    routes = []
    skipped = 0
    for r in colony.get("routes") or []:
        path_ids = [r.get("source_pin_id")] + list(r.get("waypoints") or []) + [r.get("destination_pin_id")]
        path = [index.get(int(x or 0)) for x in path_ids]
        if any(x is None for x in path) or len(path) < 2:
            skipped += 1  # a route touching the Command Center has no template form
            continue
        routes.append(Route(tuple(path), float(round(float(r.get("quantity") or 0))), int(r.get("content_type_id"))))
    pt = planet_type or 0
    layout = Layout(
        cc_level=int(colony.get("upgrade_level") or 0),
        comment=safe_comment(comment),
        diameter_km=float(round((radius_km or 0.0) * 2.0, 1)),
        planet_type_id=pt,
        pins=tuple(pins), links=tuple(links), routes=tuple(routes),
    )
    return ColonyLayout(layout, pin_ids, contents, extractors, skipped)


# ------------------------------------------------------------------ monitor
def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600.0


def project(static: StaticData, colony: ColonyLayout, last_update: Optional[datetime], now: datetime,
            radius_km: Optional[float], yield_per_head: float) -> dict:
    """Projected state of one colony now: extractor expiry, when the hubs
    are full, when imported inputs run out, idle factories. Extraction rates
    come from each extractor's real program (CCP formula) when ESI gives
    qty/cycle; otherwise from `yield_per_head`."""
    lay = colony.layout
    analysis = validate.analyse(static, lay, radius_km, yield_per_head)
    age_h = _hours(now - last_update) if last_update else None

    extractors = []
    for e in colony.extractors:
        expiry = parse_dt(e.get("expiry_time"))
        hours_left = _hours(expiry - now) if expiry else None
        extractors.append({**e, "hours_left": hours_left, "expired": hours_left is not None and hours_left <= 0,
                           "product_name": static.name(e["product_type_id"]) if e.get("product_type_id") else None})

    def vol(t: int) -> float:
        c = static.commodities.get(t)
        return c.volume if c else 0.0

    hub_pins = [i for i, k in enumerate(analysis.kinds, start=1) if k in C.HUB_KINDS]
    stored_m3 = sum(q * vol(t) for i in hub_pins for t, q in colony.contents.get(i, {}).items())
    capacity = analysis.storage_m3
    # Outputs pile up in the hubs while hauled-in inputs are used up and free
    # their space: occupancy grows by exports - imports per hour.
    net_in = analysis.export_m3_per_hour - analysis.import_m3_per_hour
    full_at = None
    if net_in > 1e-9:
        free = max(0.0, capacity - stored_m3)
        full_at = (last_update or now) + timedelta(hours=free / net_in)

    inputs_empty_at = None
    empty_type = None
    for t, rate in analysis.imports.items():
        if rate <= 1e-9:
            continue
        have = sum(colony.contents.get(i, {}).get(t, 0.0) for i in hub_pins)
        when = (last_update or now) + timedelta(hours=have / rate)
        if inputs_empty_at is None or when < inputs_empty_at:
            inputs_empty_at, empty_type = when, t

    idle = [{"pin": f, "product_name": static.name(lay.pins[f - 1].product)}
            for f, runs in analysis.runs_per_hour.items() if runs < 1e-9]
    return {
        "last_update": last_update.isoformat() if last_update else None,
        "age_hours": age_h,
        "uncertain": age_h is not None and age_h > UNCERTAIN_AFTER_HOURS,
        "extractors": extractors,
        "storage_m3": capacity, "stored_m3": stored_m3,
        "export_m3_per_hour": analysis.export_m3_per_hour, "import_m3_per_hour": analysis.import_m3_per_hour,
        "net_m3_per_hour": net_in,
        "full_at": full_at.isoformat() if full_at else None,
        "hours_until_full": _hours(full_at - now) if full_at else None,
        "inputs_empty_at": inputs_empty_at.isoformat() if inputs_empty_at else None,
        "hours_until_inputs_empty": _hours(inputs_empty_at - now) if inputs_empty_at else None,
        "inputs_empty_type": static.name(empty_type) if empty_type else None,
        "idle_factories": idle,
        "product_type_id": analysis.product_type_id,
        "product_name": static.name(analysis.product_type_id) if analysis.product_type_id else None,
        "chain": analysis.chain,
        "findings": [f.to_dict() for f in analysis.findings if f.severity != validate.INFO],
        "skipped_routes": colony.skipped_routes,
    }


# ------------------------------------------------------------------ calibration
def calibration_samples(colony: ColonyLayout) -> list[dict]:
    """Per extractor program: average P0 per head per hour, with the full
    CCP formula (noise included - P-64). Programs shorter than an hour or
    without heads/qty are skipped (P-54)."""
    out = []
    for e in colony.extractors:
        install, expiry = parse_dt(e.get("install_time")), parse_dt(e.get("expiry_time"))
        qty, cycle, heads = e.get("qty_per_cycle"), e.get("cycle_time"), int(e.get("heads") or 0)
        if not (install and expiry and qty and cycle and heads and e.get("product_type_id")):
            continue
        seconds = (expiry - install).total_seconds()
        if seconds < MIN_SAMPLE_PROGRAM_HOURS * 3600:
            continue
        per_head = decay.per_head_per_hour(int(qty), int(cycle), seconds, heads)
        if not math.isfinite(per_head) or per_head <= 0:
            continue
        out.append({
            "pin_id": e["pin_id"], "install_time": install, "p0_type_id": int(e["product_type_id"]),
            "heads": heads, "program_hours": seconds / 3600.0, "per_head_per_hour": per_head,
        })
    return out


def calibrated_yield(samples: list[dict], reference_hours: float = C.REFERENCE_PROGRAM_HOURS,
                     min_samples: int = 3) -> Optional[float]:
    """Median of samples, each scaled to the reference program length with
    the noise-free ratio, once there are at least `min_samples`."""
    if len(samples) < min_samples:
        return None
    scaled = sorted(
        s["per_head_per_hour"] / decay.program_ratio(s["program_hours"], reference_hours)
        for s in samples
    )
    mid = len(scaled) // 2
    return scaled[mid] if len(scaled) % 2 else (scaled[mid - 1] + scaled[mid]) / 2

"""PI economics: taxes, freight, setup, profit, verdict - pure.

docs/PI_PLAN.md 3.3 / docs/PI_TECHNICAL_DESIGN.md 3.6. Prices come in as a
`Prices` object (built from hubs.hub_pricing in actions.py), so this module
never touches ESI.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from . import constants as C
from .model import EXTRACTION_CHAINS, Evaluation, StaticData

# Launchpad dogma 1638 importTax = 0.5: importing costs half the export rate.
IMPORT_TAX_PIN_FACTOR = 0.5
HOURS_PER_WEEK = 168.0

# Verdict reasons - a fixed vocabulary the frontend translates and filters on.
REASON_TAX = "tax"
REASON_FREIGHT = "freight"
REASON_INPUT_COST = "input_cost"
REASON_SETUP = "setup"
REASON_EXTRACTION_LOW = "extraction_low"
REASON_MARKET_THIN = "market_thin"
REASON_BELOW_THRESHOLD = "below_threshold"
REASON_NO_PRICE = "no_price"


# ------------------------------------------------------------------ zones
def security_zone(security: Optional[float], region_id: Optional[int]) -> str:
    """high/low/null/wormhole. Wormhole first (J-space has security -1.0);
    Pochven counts as null-sec. Uses production's rounding rule (true-sec
    0.45 is high-sec), which is float4-safe since P-33 was fixed."""
    from ..production.constants import _rounded_security

    if region_id is not None and C.WORMHOLE_REGION_MIN <= int(region_id) <= C.WORMHOLE_REGION_MAX:
        return C.ZONE_WORMHOLE
    if region_id == C.POCHVEN_REGION_ID:
        return C.ZONE_NULLSEC
    if security is None:
        # Unknown system: assume the zone with the lowest yield defaults
        # rather than over-crediting an unverified location.
        return C.ZONE_HIGHSEC
    rounded = _rounded_security(security)
    if rounded >= 0.5:
        return C.ZONE_HIGHSEC
    if rounded > 0.0:
        return C.ZONE_LOWSEC
    return C.ZONE_NULLSEC


def npc_tax_rate(zone: str, customs_code_expertise: int) -> float:
    """High-sec NPC customs part: 10% minus 1% per Customs Code Expertise
    level. Outside high-sec only the owner's rate applies."""
    if zone != C.ZONE_HIGHSEC:
        return 0.0
    level = max(0, min(5, int(customs_code_expertise)))
    return max(0.0, C.HIGHSEC_NPC_TAX - C.CUSTOMS_CODE_EXPERTISE_PER_LEVEL * level)


# ------------------------------------------------------------------ prices
@dataclass(frozen=True)
class Prices:
    sell: Mapping[int, Optional[float]]          # sell-order percentile per type
    buy: Mapping[int, Optional[float]]           # buy-order percentile per type
    hub_by_type: Mapping[int, int] = field(default_factory=dict)
    daily_volume: Mapping[int, Optional[float]] = field(default_factory=dict)
    trend: Mapping[int, Optional[dict]] = field(default_factory=dict)
    source_note: Optional[str] = None     # e.g. "fell back to a Goonmetrics snapshot"


@dataclass(frozen=True)
class MarketSettings:
    broker_fee: float
    sales_tax: float
    valuation: str                 # "sell_orders" | "buy_orders"
    freight_per_m3: float
    tax_rate: float                # NPC part + owner part
    amortisation_days: float
    min_isk_per_planet_day: float
    market_share_warning: float
    program_hours: float
    interval_hours: float


def output_unit_value(type_id: int, prices: Prices, m: MarketSettings) -> Optional[float]:
    if m.valuation == "buy_orders":
        p = prices.buy.get(type_id)
        return None if p is None else p * (1 - m.sales_tax)
    p = prices.sell.get(type_id)
    return None if p is None else p * (1 - m.broker_fee - m.sales_tax)


def input_unit_cost(type_id: int, prices: Prices, m: MarketSettings) -> Optional[float]:
    p = prices.sell.get(type_id)
    return None if p is None else p * (1 + m.broker_fee)


# ------------------------------------------------------------------ result
@dataclass(frozen=True)
class Economics:
    revenue_per_day: float
    input_cost_per_day: float
    export_tax_per_day: float
    import_tax_per_day: float
    freight_per_day: float
    setup_per_day: float
    profit_per_day: float
    output_units_per_day: float
    haul_m3_per_week: float
    interactions_per_week: float
    isk_per_interaction: Optional[float]
    isk_per_m3: Optional[float]
    market_share: Optional[float]
    worth_it: bool
    reason: Optional[str]
    missing_prices: tuple[int, ...]

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def _num(x: float) -> float:
    return 0.0 if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else x


def compute(ev: Evaluation, static: StaticData, prices: Prices, m: MarketSettings) -> Economics:
    """Per-day economics of one colony. Every flow is scaled by the
    colony's effective factor: what it delivers when visited once per
    collection interval."""
    f = ev.effective_factor * 24.0
    missing: list[int] = []
    revenue = 0.0
    export_tax = 0.0
    for t, qty in ev.exports.items():
        units = qty * f
        value = output_unit_value(t, prices, m)
        if value is None:
            missing.append(t)
        else:
            revenue += units * value
        c = static.commodities.get(t)
        export_tax += units * (c.export_tax_base if c else 0.0) * m.tax_rate
    input_cost = 0.0
    import_tax = 0.0
    for t, qty in ev.imports.items():
        units = qty * f
        cost = input_unit_cost(t, prices, m)
        if cost is None:
            missing.append(t)
        else:
            input_cost += units * cost
        c = static.commodities.get(t)
        import_tax += units * (c.import_tax_base if c else 0.0) * m.tax_rate * IMPORT_TAX_PIN_FACTOR
    m3_per_day = (ev.import_m3_per_hour + ev.export_m3_per_hour) * f
    freight = m3_per_day * m.freight_per_m3
    setup = ev.setup_isk / max(m.amortisation_days, 1e-9)
    profit = revenue - input_cost - export_tax - import_tax - freight - setup

    output_units = ev.product_per_hour * f
    daily_volume = prices.daily_volume.get(ev.design.product_type_id)
    share = (output_units / daily_volume) if daily_volume else None

    restarts = len(ev.design.ecus) * HOURS_PER_WEEK / max(m.program_hours, 1.0)
    hauls = HOURS_PER_WEEK / max(m.interval_hours, 1.0) if (ev.imports or ev.exports) else 0.0
    interactions = restarts + hauls

    reason: Optional[str] = None
    if missing:
        reason = REASON_NO_PRICE
        worth = False
    elif share is not None and share > m.market_share_warning:
        reason = REASON_MARKET_THIN
        worth = False
    elif profit >= m.min_isk_per_planet_day:
        worth = True
    else:
        worth = False
        if profit > 0:
            reason = REASON_BELOW_THRESHOLD
        elif ev.design.chain in EXTRACTION_CHAINS and ev.idle_factories > 0.5:
            reason = REASON_EXTRACTION_LOW
        else:
            costs = {
                REASON_TAX: export_tax + import_tax,
                REASON_FREIGHT: freight,
                REASON_INPUT_COST: input_cost,
                REASON_SETUP: setup,
            }
            reason = max(costs, key=lambda k: costs[k])
            if ev.design.chain in EXTRACTION_CHAINS and costs[reason] < revenue * 0.2:
                reason = REASON_EXTRACTION_LOW

    return Economics(
        revenue_per_day=revenue, input_cost_per_day=input_cost,
        export_tax_per_day=export_tax, import_tax_per_day=import_tax,
        freight_per_day=freight, setup_per_day=setup, profit_per_day=profit,
        output_units_per_day=output_units,
        haul_m3_per_week=m3_per_day * 7,
        interactions_per_week=interactions,
        isk_per_interaction=(profit * 7 / interactions) if interactions > 0 else None,
        isk_per_m3=(profit / m3_per_day) if m3_per_day > 0 else None,
        market_share=share, worth_it=worth, reason=reason,
        missing_prices=tuple(sorted(set(missing))),
    )


# ------------------------------------------------------------ price trend
def trend_from_history(points: Iterable) -> Optional[dict]:
    """30-day trend (% change of the average price, first vs last week of the
    window), volatility (stdev / mean of daily averages) and average daily
    traded units. Untraded days (avg price 0) are skipped. The window is
    whatever the history source returned; `days` says how long it was
    (Goonmetrics returns ~28 days, P-36)."""
    pts = sorted((p for p in points if getattr(p, "avg_price", 0) > 0), key=lambda p: p.date)[-30:]
    if len(pts) < 2:
        return None
    prices = [p.avg_price for p in pts]
    head = statistics.mean(prices[:7])
    tail = statistics.mean(prices[-7:])
    mean = statistics.mean(prices)
    return {
        "days": len(pts),
        "change": (tail - head) / head if head else None,
        "volatility": (statistics.pstdev(prices) / mean) if mean else None,
        "avg_daily_volume": statistics.mean(p.movement for p in pts),
    }


__all__ = [
    "Economics", "MarketSettings", "Prices", "compute", "input_unit_cost", "npc_tax_rate",
    "output_unit_value", "security_zone", "trend_from_history", "_num",
]

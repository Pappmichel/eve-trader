"""Order-book depth for the mineral shopping lists (Ore & Minerals and Module
Reprocessing).

A shopping list must not recommend more of an item than sellers actually list
at an acceptable price. The acceptable price is the reference price both
lists already use (the 5th-percentile sell price, see esi_client.
_summarize_orders) plus a tolerance (default 5%). The listed volume up to
that price is split into a few price tiers, each with its real volume-weighted
price, so the optimizer pays more for deeper tiers and takes them only when
they still beat every other source (confirmed with the user 2026-11-04).

Pure: callers pass the raw sell orders they already fetched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

# Band edges above the cheapest listed order: +1%, +2%, +4%, ... up to the
# cap. Fine near the cheap end, where most units are bought, coarse further
# up; each band is one integer-program column, so a 5% tolerance gives four.
FIRST_BAND_WIDTH = 0.01


@dataclass(frozen=True)
class SupplyTier:
    """One buyable slice of an item: up to `max_units` at `unit_cost` (landed,
    ISK per unit). `max_units` None means the depth is unknown (a Goonmetrics
    home-market quote) and the slice is not capped."""
    unit_cost: float
    max_units: Optional[int]
    source: Optional[str] = None
    hub_region_id: Optional[int] = None

    @property
    def depth_checked(self) -> bool:
        return self.max_units is not None


def _band_edges(low: float, cap: float) -> list[float]:
    edges = []
    step = FIRST_BAND_WIDTH
    while low * (1 + step) < cap:
        edges.append(low * (1 + step))
        step *= 2
    edges.append(cap)
    return edges


def sell_price_tiers(orders: list[dict], reference_price: Optional[float],
                     tolerance: float) -> list[tuple[float, int]]:
    """[(volume-weighted price, units)] of the sell orders priced at or below
    reference_price x (1 + tolerance), cheapest first, merged into price bands
    (see FIRST_BAND_WIDTH). Empty when nothing qualifies."""
    if reference_price is None or reference_price <= 0:
        return []
    cap = reference_price * (1 + max(0.0, tolerance))
    sells = sorted((float(o["price"]), int(o["volume_remain"])) for o in orders
                   if not o.get("is_buy_order") and o.get("volume_remain", 0) > 0 and float(o["price"]) <= cap)
    if not sells:
        return []
    edges = _band_edges(sells[0][0], cap)
    bands = [[0.0, 0] for _ in edges]  # [isk, units]
    band = 0
    for price, volume in sells:
        while price > edges[band]:
            band += 1
        bands[band][0] += price * volume
        bands[band][1] += volume
    return [(isk / units, int(units)) for isk, units in bands if units > 0]


def supply_tiers(orders: list[dict], reference_price: Optional[float], tolerance: float,
                 landed: Callable[[float], Optional[float]], source: Optional[str] = None,
                 hub_region_id: Optional[int] = None) -> list[SupplyTier]:
    """sell_price_tiers turned into landed SupplyTiers. `landed` maps a hub
    sell price to the landed ISK per unit (broker fee, freight) exactly as the
    caller prices its reference."""
    tiers = []
    for price, units in sell_price_tiers(orders, reference_price, tolerance):
        cost = landed(price)
        if cost is not None:
            tiers.append(SupplyTier(unit_cost=cost, max_units=units, source=source, hub_region_id=hub_region_id))
    return tiers


def sell_orders_by_type(client, region_by_type: dict[int, int]) -> dict[int, list[dict]]:
    """Raw orders per type from the region each type was priced at. The
    order book is cached by ESIClient.region_orders_raw, so after pricing this
    costs no further ESI calls."""
    by_region: dict[int, list[int]] = {}
    for type_id, region_id in region_by_type.items():
        if region_id:
            by_region.setdefault(region_id, []).append(type_id)
    orders: dict[int, list[dict]] = {}
    for region_id, type_ids in by_region.items():
        orders.update(client.region_orders_raw_bulk(region_id, sorted(type_ids)))
    return orders

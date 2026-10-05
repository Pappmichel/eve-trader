"""Trade hubs shared by every tool (GitHub issue #222).

Each tool has its own hub setting (`hub_region_id`; Trading keeps its
historical `jita_region_id`). The non-Trading tools may also pick ALL_HUBS:
then every item is priced at whichever of the four hubs gives the lowest
landed cost - hub sell price plus buy broker fee plus that hub's freight to
the destination structure.

Freight per hub comes from one tenant-wide table,
`TradingConfig.hub_freight_cost_per_m3` (region id as string -> ISK/m3),
shared by every tool in ALL_HUBS mode. A hub without an entry falls back to
the calling tool's own single freight value. In single-hub mode a tool keeps
using its own freight value, unchanged.

`hub_pricing` is the one entry point: it returns the order-book stats to
price each item with, the hub that won it and the freight rate to use, so a
tool computes landed cost the same way in both modes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from .config import TRADING_CONFIG, TradingConfig
from .esi_client import OrderStats

# Sentinel for a tool's hub setting: price every item at its best hub.
ALL_HUBS = 0

# The four NPC trade hubs, by region id (same list as frontend/src/tradingHubs.ts).
TRADE_HUBS: dict[int, str] = {
    10000002: "Jita",
    10000043: "Amarr",
    10000032: "Dodixie",
    10000030: "Rens",
}


def hub_name(region_id: Optional[int]) -> str:
    if region_id is None:
        return "-"
    return TRADE_HUBS.get(region_id, f"Region {region_id}")


def hub_freight_per_m3(region_id: int, fallback: float,
                       cfg: TradingConfig = TRADING_CONFIG) -> float:
    """Freight from `region_id`'s hub to the structure, from the shared
    table; `fallback` (the tool's own freight value) when it has no entry."""
    rate = (cfg.hub_freight_cost_per_m3 or {}).get(str(region_id))
    return float(rate) if rate is not None else float(fallback)


@dataclass
class HubPricing:
    """Order-book stats per type_id, plus which hub each one came from and
    the freight rate (ISK/m3) that goes with that hub."""
    stats: dict[int, OrderStats] = field(default_factory=dict)
    hub_by_type: dict[int, int] = field(default_factory=dict)
    freight_by_type: dict[int, float] = field(default_factory=dict)

    def landed(self, type_id: int, volume_m3: float, broker_fee: float) -> Optional[float]:
        s = self.stats.get(type_id)
        if s is None or s.sell_percentile is None:
            return None
        return s.sell_percentile * (1 + broker_fee) + self.freight_by_type.get(type_id, 0.0) * volume_m3


def hub_pricing(client, hub_region_id: int, type_ids: Iterable[int],
                volumes: Mapping[int, float], broker_fee: float, freight_fallback: float,
                cfg: TradingConfig = TRADING_CONFIG,
                freight_override: Optional[float] = None) -> HubPricing:
    """Stats for `type_ids` at `hub_region_id`, or - for ALL_HUBS - at the
    hub with the lowest landed cost per item (sell percentile x (1 +
    broker_fee) + that hub's freight x volume). An item no hub sells stays
    in `stats` with an empty OrderStats from the first hub, so callers see
    "no price" exactly as in single-hub mode.

    `freight_override`: one ISK/m3 rate for every hub instead of the shared
    hub -> home-structure table. The PI tool passes its own rate: its goods
    travel between a hub and planets, not to the home structure
    (docs/PI_TECHNICAL_DESIGN.md P-34)."""
    ids = sorted(set(type_ids))
    if hub_region_id != ALL_HUBS:
        stats = client.region_order_stats_bulk(hub_region_id, ids) if ids else {}
        return HubPricing(
            stats=dict(stats),
            hub_by_type={t: hub_region_id for t in stats},
            freight_by_type={t: float(freight_fallback) for t in stats},
        )

    result = HubPricing()
    if not ids:
        return result
    best_landed: dict[int, float] = {}
    for region_id in TRADE_HUBS:
        freight = (float(freight_override) if freight_override is not None
                   else hub_freight_per_m3(region_id, freight_fallback, cfg))
        for type_id, s in client.region_order_stats_bulk(region_id, ids).items():
            if s.sell_percentile is None:
                if type_id not in result.stats:
                    result.stats[type_id] = s
                    result.hub_by_type[type_id] = region_id
                    result.freight_by_type[type_id] = freight
                continue
            landed = s.sell_percentile * (1 + broker_fee) + freight * float(volumes.get(type_id) or 0.0)
            if type_id not in best_landed or landed < best_landed[type_id]:
                best_landed[type_id] = landed
                result.stats[type_id] = s
                result.hub_by_type[type_id] = region_id
                result.freight_by_type[type_id] = freight
    return result


def do_get_hub_freight(cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """The shared freight table, one row per trade hub (None = no entry, the
    tool's own freight value applies)."""
    rates = cfg.hub_freight_cost_per_m3 or {}
    return [{"region_id": rid, "hub": name, "freight_cost_per_m3": rates.get(str(rid))}
            for rid, name in TRADE_HUBS.items()]


def do_update_hub_freight(rates: Mapping[int, Optional[float]],
                          cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Replaces the shared freight table. A None rate removes that hub's
    entry. Stored on TradingConfig (tenant_settings scope "trading") like
    jita_buy_broker_fee, which every tool already shares from there."""
    from .actions import ActionError
    from .config import ConfigError, save_tenant_config_overrides, validate_trading_overrides

    table = {str(int(rid)): float(rate) for rid, rate in rates.items() if rate is not None}
    updates = {"hub_freight_cost_per_m3": table}
    try:
        validate_trading_overrides(updates)
        save_tenant_config_overrides("trading", updates, cfg, cfg_type=TradingConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return do_get_hub_freight(cfg)

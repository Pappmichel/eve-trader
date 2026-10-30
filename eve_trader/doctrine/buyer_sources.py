"""Buyer-side sources for the Doctrine Shopping List.

Stock the configured buyer characters (`DoctrineConfig.buyer_character_ids`)
already hold anywhere in Doctrine's hub region (`hub_region_id`; every trade
hub region for All hubs), and what they still have on open buy orders there,
does not have to be bought again. Both only reduce the Shopping List's
"to buy" figure; the Stockpile status keeps counting the real stockpile
location only. The hub region is the whole region on purpose: buyers place
orders at player structures next to the hub (e.g. Perimeter), and filled
orders deliver to the order's location.

Fail-closed like every other ESI read: a buyer counts only through rows
shared with `doctrine` (Assets for the hangar, Market Orders for the buy
orders). A missing share, token or ESI failure becomes a warning, never an
error for the whole page.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .. import hubs, storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from ..esi_data.access import is_shared, read_esi
from ..esi_data.selector import select_auth_role
from ..locations import reader
from .config import DoctrineConfig

_ORDERS_SCOPE = "esi-markets.read_character_orders.v1"


@dataclass
class BuyerStock:
    hub_hangar: dict[int, float] = field(default_factory=dict)
    on_order: dict[int, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class BuyOrders:
    by_type: dict[int, float] = field(default_factory=dict)
    # Every location (buy or sell order) the buyers trade at inside the hub
    # regions - tells hub_hangar_stock a structure's region when the
    # location registry has no solar system for it.
    hub_locations: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def hub_regions(cfg: DoctrineConfig) -> set[int]:
    if cfg.hub_region_id == hubs.ALL_HUBS:
        return set(hubs.TRADE_HUBS)
    return {cfg.hub_region_id}


def _character_name(tm: TokenManager, character_id: int) -> str:
    for rec in tm.list_records():
        if rec.character_id == character_id and rec.character_name:
            return rec.character_name
    return str(character_id)


def hub_hangar_stock(type_ids: list[int], buyer_ids: set[int], regions: set[int],
                     known_hub_locations: set[int], exclude_location_id: Optional[int]) -> dict[int, float]:
    """{type_id: qty} the buyers hold in the hub regions (containers counted at
    their structure, assembled ships and fitted items excluded -
    storage.esi_stock_from_asset_rows). `exclude_location_id` (the stockpile
    location) is skipped, it is already counted as stockpile."""
    rows = [r for r in read_esi("assets", "doctrine")
            if r.get("owner_type") == "character" and r.get("owner_id") in buyer_ids
            and r.get("resolved_location_id") is not None
            and r.get("resolved_location_id") != exclude_location_id]
    locations = {int(r["resolved_location_id"]) for r in rows}
    info = reader.describe(locations - known_hub_locations)
    in_hub = {loc for loc in locations
              if loc in known_hub_locations or (loc in info and info[loc].region_id in regions)}
    rows = [r for r in rows if r["resolved_location_id"] in in_hub]
    return storage.esi_stock_from_asset_rows(rows, type_ids, None)


def open_buy_orders(type_ids: list[int], buyer_ids: set[int], regions: set[int], tm: TokenManager,
                    client: Optional[ESIClient] = None) -> BuyOrders:
    """volume_remain per type over the buyers' open buy orders in the hub
    regions, read live from ESI, plus a warning per buyer that could not be read."""
    wanted = set(type_ids)
    totals: dict[int, float] = defaultdict(float)
    result = BuyOrders()
    for character_id in sorted(buyer_ids):
        name = _character_name(tm, character_id)
        if not is_shared("market_orders", "doctrine", "character", character_id):
            result.warnings.append(f"{name}: Market Orders are not shared with Doctrine - buy orders not counted.")
            continue
        role = select_auth_role(character_id, _ORDERS_SCOPE, tokens=tm)
        if role is None:
            result.warnings.append(f"{name}: no token holds {_ORDERS_SCOPE} - buy orders not counted.")
            continue
        client = client or ESIClient(tokens=tm)
        try:
            orders = client.character_orders(character_id, auth_role=role)
        except ESIError:
            result.warnings.append(f"{name}: buy orders could not be read from ESI - not counted.")
            continue
        for o in orders:
            if o.get("region_id") not in regions:
                continue
            if o.get("location_id") is not None:
                result.hub_locations.add(int(o["location_id"]))
            if o.get("is_buy_order") and o.get("type_id") in wanted:
                totals[o["type_id"]] += o.get("volume_remain") or 0
    result.by_type = dict(totals)
    return result


def collect(type_ids: list[int], cfg: DoctrineConfig) -> Optional[BuyerStock]:
    """None when no buyer is configured (feature off)."""
    buyer_ids = {int(c) for c in cfg.buyer_character_ids}
    if not buyer_ids or not type_ids:
        return None
    tm = TokenManager(OAUTH_CONFIG)
    regions = hub_regions(cfg)
    orders = open_buy_orders(type_ids, buyer_ids, regions, tm)
    result = BuyerStock(on_order=orders.by_type, warnings=list(orders.warnings))
    result.hub_hangar = hub_hangar_stock(type_ids, buyer_ids, regions, orders.hub_locations,
                                         cfg.effective_stockpile_location_id)
    for character_id in sorted(buyer_ids):
        if not is_shared("assets", "doctrine", "character", character_id):
            result.warnings.append(f"{_character_name(tm, character_id)}: Assets are not shared with "
                                   "Doctrine - hub hangar not counted.")
    return result

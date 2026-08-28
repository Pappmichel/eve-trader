"""Own open market order monitoring for Station Trading - "am I still the
best price on either side of my own orders at Jita's trade hub station."

Near-mirror of eve_trader/own_orders.py's check_undercut_pooled (see that
function's own docstring for the full "why order_id cross-referencing, not
price/type_id matching" reasoning - identical here), generalized two ways:
scoped to `cfg.station_id` (Jita's NPC trade hub) instead of a player
structure, and duplicated for the buy side, which own_orders.py's version
deliberately never covers (see config.py's own comment on
structure_market_slug: undercut-checking is inherently live-order-book-only,
never a Goonmetrics snapshot).

Jita's trade hub is a public NPC station - no auth_role/docking access is
needed to read the competing side of the book (client.region_orders_raw),
only to read the trader's *own* orders (client.character_orders, already
scoped by esi-markets.read_character_orders.v1 on every registered trader
character).
"""
from __future__ import annotations

from ..config import TRADING_CONFIG
from ..esi_client import ESIClient
from .config import StationTradingConfig


def check_undercut_pooled(traders: list[tuple[int, str]], client: ESIClient,
                           cfg: StationTradingConfig) -> list[dict]:
    """Sell side: pools every registered trader character's own sell orders
    at cfg.station_id, flags any that a genuinely different market
    participant beats on price. Returns one dict per undercut order:
    {"type_id", "my_price", "competitor_price", "difference"}."""
    my_orders = []
    for character_id, auth_role in traders:
        my_orders.extend(
            o for o in client.character_orders(character_id, auth_role=auth_role)
            if not o.get("is_buy_order") and o.get("location_id") == cfg.station_id
        )
    return _check_side(my_orders, client, cfg, is_buy_order=False)


def check_buy_undercut_pooled(traders: list[tuple[int, str]], client: ESIClient,
                               cfg: StationTradingConfig) -> list[dict]:
    """Buy side mirror of check_undercut_pooled - "outbid" here means a
    competing buy order now offers *more* than my own best bid, the
    opposite comparison direction from the sell side."""
    my_orders = []
    for character_id, auth_role in traders:
        my_orders.extend(
            o for o in client.character_orders(character_id, auth_role=auth_role)
            if o.get("is_buy_order") and o.get("location_id") == cfg.station_id
        )
    return _check_side(my_orders, client, cfg, is_buy_order=True)


def _check_side(my_orders: list[dict], client: ESIClient, cfg: StationTradingConfig,
                 is_buy_order: bool) -> list[dict]:
    if not my_orders:
        return []
    my_order_ids = {o["order_id"] for o in my_orders}
    my_best_price: dict[int, float] = {}
    for o in my_orders:
        type_id = o["type_id"]
        price = o["price"]
        if is_buy_order:
            if type_id not in my_best_price or price > my_best_price[type_id]:
                my_best_price[type_id] = price
        else:
            if type_id not in my_best_price or price < my_best_price[type_id]:
                my_best_price[type_id] = price

    competitor_best: dict[int, float] = {}
    for type_id in my_best_price:
        for o in client.region_orders_raw(TRADING_CONFIG.jita_region_id, type_id):
            if (bool(o.get("is_buy_order")) != is_buy_order
                    or o.get("location_id") != cfg.station_id
                    or o.get("order_id") in my_order_ids):
                continue
            price = o.get("price")
            if type_id not in competitor_best:
                competitor_best[type_id] = price
            elif is_buy_order:
                competitor_best[type_id] = max(competitor_best[type_id], price)
            else:
                competitor_best[type_id] = min(competitor_best[type_id], price)

    results = []
    for type_id, my_price in my_best_price.items():
        competitor_price = competitor_best.get(type_id)
        if competitor_price is None:
            continue
        beaten = competitor_price > my_price if is_buy_order else competitor_price < my_price
        if beaten:
            results.append({
                "type_id": type_id, "my_price": my_price, "competitor_price": competitor_price,
                "difference": abs(my_price - competitor_price),
            })
    results.sort(key=lambda r: r["difference"], reverse=True)
    return results

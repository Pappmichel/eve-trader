"""Own open market orders.

    Own Orders Remaining =
        SUM(Remaining) WHERE ItemID = shortlist.item_id
                         AND Buy? = FALSE
                         AND LocationID = structure_id
"""
from __future__ import annotations

from collections import defaultdict

from . import storage
from .config import TRADING_CONFIG, TradingConfig
from .esi_client import ESIClient

JITA_SOLAR_SYSTEM_ID = 30000142  # stable, never changes - distinct from cfg.jita_region_id (The Forge region)


def fetch_own_sell_orders(character_id: int, auth_role: str, client: ESIClient,
                           cfg: TradingConfig = TRADING_CONFIG) -> dict[int, float]:
    """Returns {item_id: remaining_volume} for open SELL orders at cfg.structure_id.

    Equivalent of building 'Own Orders Raw' and then computing column F for
    every shortlist row in one pass instead of one SUMIFS per row.
    """
    orders = client.character_orders(character_id, auth_role=auth_role)
    remaining: dict[int, float] = defaultdict(float)
    for o in orders:
        if o.get("is_buy_order"):
            continue
        if o.get("location_id") != cfg.structure_id:
            continue
        remaining[o["type_id"]] += o.get("volume_remain", 0)
    return dict(remaining)


def check_undercut_pooled(sellers: list[tuple[int, str]], client: ESIClient,
                           cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Same idea as check_undercut, but pools every registered seller
    character's own orders together (GitHub issue #46: multiple seller
    characters share the same structure's order slots) - `sellers` is a list
    of (character_id, auth_role) pairs. A cheaper order from one of *your
    own* other seller characters must not itself count as "undercut", only a
    genuinely different market participant's order should - my_order_ids
    below is the union across every seller passed in, so it excludes all of
    them from the competitor comparison, not just whichever one happened to
    be checked.

    ESI's structure order book (structure_orders_raw) has no owning-character
    field on each order (a real ESI limitation, confirmed against the
    endpoint's response schema - just order_id/price/type_id/volume_remain/
    etc, no character_id), so "which orders are mine" can only be determined
    by cross-referencing order_id against character_orders (which *does*
    return your own order_id per order) - not by type_id/price matching,
    which could false-positive on a coincidentally-identical price from
    another seller.

    Returns one dict per undercut order: {"type_id", "my_price",
    "competitor_price", "difference"} - only orders that are actually beaten
    (competitor_price < my_price) are included, same "only flag real problems"
    shape as fetch_seller_stock_without_order. Item names aren't resolved
    here (callers already have an SDE-backed name lookup, see actions.py
    do_check_undercut)."""
    my_orders = []
    for character_id, auth_role in sellers:
        my_orders.extend(
            o for o in client.character_orders(character_id, auth_role=auth_role)
            if not o.get("is_buy_order") and o.get("location_id") == cfg.structure_id
        )
    if not my_orders:
        return []
    my_order_ids = {o["order_id"] for o in my_orders}
    my_best_price: dict[int, float] = {}
    for o in my_orders:
        type_id = o["type_id"]
        if type_id not in my_best_price or o["price"] < my_best_price[type_id]:
            my_best_price[type_id] = o["price"]

    # The structure's order book is one shared/global fetch - any one of the
    # registered sellers with docking access can retrieve it, so the first
    # one passed in is enough (matches structure_order_stats_bulk's own
    # "pick any one seller" reasoning in actions.py).
    all_orders = client.structure_orders_raw(cfg.structure_id, auth_role=sellers[0][1])
    competitor_best: dict[int, float] = {}
    for o in all_orders:
        if o.get("is_buy_order") or o.get("order_id") in my_order_ids:
            continue
        type_id = o.get("type_id")
        if type_id not in my_best_price:
            continue  # not one of my listed items - irrelevant to this check
        price = o.get("price")
        if type_id not in competitor_best or price < competitor_best[type_id]:
            competitor_best[type_id] = price

    results = []
    for type_id, my_price in my_best_price.items():
        competitor_price = competitor_best.get(type_id)
        if competitor_price is not None and competitor_price < my_price:
            results.append({
                "type_id": type_id, "my_price": my_price, "competitor_price": competitor_price,
                "difference": my_price - competitor_price,
            })
    results.sort(key=lambda r: r["difference"], reverse=True)
    return results


def check_undercut(character_id: int, auth_role: str, client: ESIClient,
                    cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Single-seller convenience wrapper around check_undercut_pooled - see
    that function's docstring for the real logic."""
    return check_undercut_pooled([(character_id, auth_role)], client, cfg)


def fetch_seller_stock_without_order_pooled(sellers: list[tuple[int, str]], client: ESIClient,
                                             shortlist_item_ids: set[int],
                                             cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Same idea as fetch_seller_stock_without_order, but pools physical
    stock and sell-order coverage across every registered seller character
    (GitHub issue #46) - `sellers` is a list of (character_id, auth_role)
    pairs. "No sell order at all" now means none of the registered sellers'
    own orders cover it, not just one particular character's - and
    asset_quantity is the combined total across every seller's own hangar,
    since the structure's hangar/assets are shared regardless of which
    character happens to hold them.

    See fetch_seller_stock_without_order's own docstring for the scope/scope
    caveats (shortlist-only, complete-absence-only, ESI scope requirement,
    one-level-flat asset limitation) - unchanged here, just pooled."""
    asset_qty: dict[int, float] = defaultdict(float)
    sell_remaining: dict[int, float] = defaultdict(float)
    for character_id, auth_role in sellers:
        assets = client.character_assets(character_id, auth_role=auth_role)
        for a in assets:
            type_id = a.get("type_id")
            if type_id not in shortlist_item_ids:
                continue
            if a.get("location_id") != cfg.structure_id:
                continue
            if a.get("location_flag") in storage.NON_STOCK_LOCATION_FLAGS:
                continue
            asset_qty[type_id] += a.get("quantity", 0)
        for type_id, remaining in fetch_own_sell_orders(character_id, auth_role, client, cfg).items():
            sell_remaining[type_id] += remaining

    rows = []
    for type_id, qty in asset_qty.items():
        if sell_remaining.get(type_id, 0.0) <= 0:
            rows.append({
                "type_id": type_id, "asset_quantity": qty,
                "sell_order_remaining": 0.0, "unlisted_quantity": qty,
            })
    return rows


def fetch_seller_stock_without_order(character_id: int, auth_role: str, client: ESIClient,
                                      shortlist_item_ids: set[int],
                                      cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Shortlist items the seller physically has sitting at the structure
    (personal assets, location_id == cfg.structure_id, excluding
    storage.NON_STOCK_LOCATION_FLAGS - AssetSafety/Deliveries/etc. aren't
    actually available to list) that have NO open sell order there at all
    right now - stock that could be listed for sale but apparently was
    forgotten. Only considers `shortlist_item_ids` (confirmed scope: not
    every asset at the structure, just the ones you're actually tracking as
    tradeable) and only a *complete* absence of a sell order (confirmed
    scope: a partially-listed item doesn't count, only zero coverage).
    Returns one dict per flagged type_id:
    {"type_id", "asset_quantity", "sell_order_remaining" (always 0),
    "unlisted_quantity"} (unlisted_quantity == asset_quantity here, kept as
    its own field for a stable row shape).

    Requires esi-assets.read_assets.v1 on the seller's token in addition to
    the existing esi-markets.read_character_orders.v1 - a seller character
    authorized before this scope was added needs to be re-added.

    Known limitation: only sees items sitting directly in the structure's own
    location_id (e.g. "Hangar") - items nested inside a container or a parked
    ship at the structure have their own location_id (the container's/ship's
    item_id), same one-level-flat limitation fetch_own_sell_orders has, and
    unlike storage.esi_stock_at_location's corp-office unwrap (this fetches a
    personal seller's assets, not a corp's, so there's no office to unwrap).

    Single-seller convenience wrapper around
    fetch_seller_stock_without_order_pooled - see that function's docstring
    for the multi-character logic."""
    return fetch_seller_stock_without_order_pooled([(character_id, auth_role)], client, shortlist_item_ids, cfg)


def fetch_buyer_already_covered(character_id: int, auth_role: str, client: ESIClient,
                                 cfg: TradingConfig = TRADING_CONFIG) -> set[int]:
    """Returns the set of item_ids the buyer either already has an open BUY
    order for in the Jita region or at the destination structure
    (cfg.structure_id, "C-J"), or already holds in inventory at a Jita
    station or at the destination structure - all mean "don't need to import
    more of this right now", extending the existing seller-sell-order-based
    "Already ordered" check.

    Requires esi-assets.read_assets.v1 on the buyer's token in addition to the
    existing esi-markets.read_character_orders.v1 - a buyer character
    authorized before this scope was added needs to be re-added.
    """
    covered: set[int] = set()

    orders = client.character_orders(character_id, auth_role=auth_role)
    for o in orders:
        if not o.get("is_buy_order"):
            continue
        if o.get("region_id") == cfg.jita_region_id or o.get("location_id") == cfg.structure_id:
            covered.add(o["type_id"])

    jita_stations = storage.get_station_ids_in_system(JITA_SOLAR_SYSTEM_ID)
    assets = client.character_assets(character_id, auth_role=auth_role)
    for a in assets:
        if a.get("location_id") in jita_stations or a.get("location_id") == cfg.structure_id:
            covered.add(a["type_id"])

    return covered

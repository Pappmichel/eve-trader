"""Buyer-side sources for the Doctrine Shopping List.

Stock the configured buyer characters (`DoctrineConfig.buyer_character_ids`)
already hold in Jita 4-4, and what they still have on open buy orders there,
does not have to be bought again. Both only reduce the Shopping List's
"to buy" figure; the Stockpile status keeps counting the real stockpile
location only.

Fail-closed like every other ESI read: a buyer counts only through rows
shared with `doctrine` (Assets for the hangar, Market Orders for the buy
orders). A missing share, token or ESI failure becomes a warning, never an
error for the whole page.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from ..esi_data.access import is_shared, read_esi
from ..esi_data.selector import select_auth_role
from .config import DoctrineConfig

JITA_44_STATION_ID = 60003760
_ORDERS_SCOPE = "esi-markets.read_character_orders.v1"


@dataclass
class BuyerStock:
    jita_hangar: dict[int, float] = field(default_factory=dict)
    on_order: dict[int, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _character_name(tm: TokenManager, character_id: int) -> str:
    for rec in tm.list_records():
        if rec.character_id == character_id and rec.character_name:
            return rec.character_name
    return str(character_id)


def jita_hangar_stock(type_ids: list[int], buyer_ids: set[int]) -> dict[int, float]:
    """{type_id: qty} the buyers hold in Jita 4-4 (containers included,
    assembled ships and fitted items excluded - storage.esi_stock_from_asset_rows)."""
    rows = [r for r in read_esi("assets", "doctrine")
            if r.get("owner_type") == "character" and r.get("owner_id") in buyer_ids]
    return storage.esi_stock_from_asset_rows(rows, type_ids, JITA_44_STATION_ID)


def open_buy_orders(type_ids: list[int], buyer_ids: set[int], tm: TokenManager,
                    client: Optional[ESIClient] = None) -> tuple[dict[int, float], list[str]]:
    """{type_id: volume_remain} over the buyers' open buy orders at Jita 4-4,
    read live from ESI, plus a warning per buyer that could not be read."""
    wanted = set(type_ids)
    totals: dict[int, float] = defaultdict(float)
    warnings: list[str] = []
    for character_id in sorted(buyer_ids):
        name = _character_name(tm, character_id)
        if not is_shared("market_orders", "doctrine", "character", character_id):
            warnings.append(f"{name}: Market Orders are not shared with Doctrine - buy orders not counted.")
            continue
        role = select_auth_role(character_id, _ORDERS_SCOPE, tokens=tm)
        if role is None:
            warnings.append(f"{name}: no token holds {_ORDERS_SCOPE} - buy orders not counted.")
            continue
        client = client or ESIClient(tokens=tm)
        try:
            orders = client.character_orders(character_id, auth_role=role)
        except ESIError:
            warnings.append(f"{name}: buy orders could not be read from ESI - not counted.")
            continue
        for o in orders:
            if not o.get("is_buy_order") or o.get("location_id") != JITA_44_STATION_ID:
                continue
            if o.get("type_id") in wanted:
                totals[o["type_id"]] += o.get("volume_remain") or 0
    return dict(totals), warnings


def collect(type_ids: list[int], cfg: DoctrineConfig) -> Optional[BuyerStock]:
    """None when no buyer is configured (feature off)."""
    buyer_ids = {int(c) for c in cfg.buyer_character_ids}
    if not buyer_ids or not type_ids:
        return None
    tm = TokenManager(OAUTH_CONFIG)
    result = BuyerStock()
    if cfg.effective_stockpile_location_id == JITA_44_STATION_ID:
        # The stockpile itself already counts Jita 4-4.
        result.warnings.append("The stockpile location is Jita 4-4 - the buyers' hangar is already counted there.")
    else:
        result.jita_hangar = jita_hangar_stock(type_ids, buyer_ids)
        for character_id in sorted(buyer_ids):
            if not is_shared("assets", "doctrine", "character", character_id):
                result.warnings.append(f"{_character_name(tm, character_id)}: Assets are not shared with "
                                       "Doctrine - Jita 4-4 hangar not counted.")
    result.on_order, order_warnings = open_buy_orders(type_ids, buyer_ids, tm)
    result.warnings.extend(order_warnings)
    return result

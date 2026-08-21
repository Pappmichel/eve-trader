from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Candidate:
    """One row of 'Candidate Universe' / 'Focused Candidates'."""
    item: str
    type_id: int
    volume_m3: float
    category: str            # real SDE category name (e.g. "Implant", "Drone", "Material" - see candidate_discovery.guess_category), "Module/Rig"/"Material" only as a fallback
    market_group_path: str
    meta_level: Optional[int] = None    # EVE "metaLevel" dogma attribute (0=Tech I, 5=Tech II, ...)


@dataclass
class ShortlistItem:
    """One shortlist entry: a candidate item actively tracked for import."""
    item: str
    item_id: int
    category: str
    volume_m3: float
    active: bool = True
    meta_level: Optional[int] = None


@dataclass
class ShortlistRow:
    """Fully computed row, mirrors columns A-O of Import Analysis Clean."""
    item: str
    category: str
    landed_cost: Optional[float]
    net_sell: Optional[float]
    # Currently-listed sell-order quantity at the structure (order-book
    # depth right now), NOT actual daily traded volume - see GitHub issue
    # #51 / esi_client.OrderStats and CLAUDE.md's "Theoretical ceiling"
    # section.
    sell_volume: Optional[float]
    own_orders_remaining: float
    profit_per_unit: Optional[float]
    margin: Optional[float]
    profit_per_m3: Optional[float]
    decision: str
    active: bool
    item_id: int
    volume_m3: float
    jita_sell: Optional[float]
    import_cost: Optional[float]
    meta_level: Optional[int] = None


@dataclass
class NewCandidateResult:
    """One row of 'New Candidates' (FindNewImportCandidates output)."""
    item: str
    category: str
    type_id: int
    volume_m3: float
    paired_days: int
    profitable_days: int
    hit_rate: float
    latest_margin: float
    best_margin: float
    avg_profit_m3: float
    avg_sell_movement: float
    score: float
    recommendation: str
    add: bool
    meta_level: Optional[int] = None


@dataclass
class RealizedTrade:
    """A matched buy (Jita) / sell (structure) pair for the same type_id."""
    type_id: int
    item: str
    buy_date: str
    buy_qty: int
    buy_unit_price: float
    sell_date: str
    sell_qty: int
    sell_unit_price: float
    matched_qty: int
    realized_profit: float
    margin: float


@dataclass
class UnlistedStockRow:
    """Stock physically sitting at the structure that isn't (fully) covered
    by an open sell order - see own_orders.fetch_seller_stock_without_order.
    sell_volume/margin mirror ShortlistRow's own fields (same
    shortlist.evaluate_shortlist_item formula) - None when the item has no
    Jita/C-J order-book data at all (e.g. never priced through the
    shortlist)."""
    type_id: int
    item: str
    asset_quantity: float
    sell_order_remaining: float
    unlisted_quantity: float
    sell_volume: Optional[float] = None
    margin: Optional[float] = None


@dataclass
class UndercutRow:
    """One of the seller's own sell orders currently beaten by a cheaper
    competing order at the same structure - see own_orders.check_undercut."""
    type_id: int
    item: str
    my_price: float
    competitor_price: float
    difference: float

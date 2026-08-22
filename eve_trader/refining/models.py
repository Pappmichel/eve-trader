"""Plain dataclasses for the Ore Shortlist (GitHub issue #91) - mirrors
eve_trader/models.py's ShortlistItem/ShortlistRow shape for Trading's own
shortlist, but for compressed ore/ice import+refine+sell instead of plain
buy-low-sell-high arbitrage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class OreCandidate:
    """One compressed ore/ice type from the SDE category/group filter (see
    refining/candidate_discovery.py) - the fixed, auto-derived universe this
    tool's shortlist is built from (unlike Trading's own candidate search,
    there's no manual add-one-by-one step, see #91's scope decision)."""
    type_id: int
    item: str
    family: str          # e.g. "Veldspar" - RefiningConfig.ore_family_skill_levels lookup key
    is_ice: bool
    volume_m3: float      # the compressed type's own SDE volume (already reflects compression)


@dataclass
class OreShortlistItem:
    """One Ore Shortlist entry: a compressed ore/ice candidate actively tracked."""
    item_id: int
    item: str
    family: str
    is_ice: bool
    active: bool = True


@dataclass
class OreShortlistRow:
    """Fully computed row - mirrors eve_trader/models.py's ShortlistRow shape."""
    item_id: int
    item: str
    family: str
    is_ice: bool
    active: bool
    volume_m3: Optional[float]
    landed_cost: Optional[float]      # Jita buy percentile x (1 + broker fee) + haul cost
    yield_pct: Optional[float]        # refining/engine.py's ore_ice_yield for this item's family
    mineral_value: Optional[float]    # sum(mineral_qty x C-J sell percentile x structure_sell_haircut)
    refining_tax: Optional[float]
    net_sell: Optional[float]         # mineral_value - refining_tax
    sell_listed_qty: Optional[float]  # currently-listed sell-order quantity at C-J for this ore/ice type itself
    profit_per_unit: Optional[float]
    margin: Optional[float]
    profit_per_m3: Optional[float]
    decision: str

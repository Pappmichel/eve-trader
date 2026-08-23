"""Plain dataclasses for the Ore Shortlist (GitHub issue #91) - mirrors
eve_trader/models.py's ShortlistItem/ShortlistRow shape for Trading's own
shortlist, but for compressed ore/ice import+refine+sell instead of plain
buy-low-sell-high arbitrage. GitHub issue #93's Mineral Shopping List types
(MineralRequirement and the optimizer's inputs/outputs) live here too, same
style.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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


# ------------------------------------------- Mineral Shopping List (issue #93)
@dataclass
class MineralRequirement:
    """One "I need this many units of this mineral" line - the optimizer's
    right-hand side. Persisted per-tenant in mineral_requirements (see
    storage.replace_mineral_requirements); entered manually today, populated
    from Production's own buy-list shortfall from #94 on."""
    type_id: int
    name: str
    required_qty: float


@dataclass
class OreOption:
    """One buyable compressed ore/ice type, fully priced and pre-refined into
    a per-whole-portion mineral yield - one column of the LP's constraint
    matrix. `yield_per_portion` is what ONE whole portion (portion_size units)
    actually reprocesses into at this tenant's configured yield%, already
    net of the structure's refining tax (see optimizer.py's module docstring)
    and already floored per material the way EVE itself rounds - the LP never
    re-derives any of that."""
    type_id: int
    item: str
    family: str
    is_ice: bool
    volume_m3: float
    portion_size: int
    landed_cost_per_unit: float
    yield_per_portion: dict[int, int] = field(default_factory=dict)

    @property
    def landed_cost_per_portion(self) -> float:
        return self.landed_cost_per_unit * self.portion_size


@dataclass
class MineralOption:
    """A required mineral's own direct-buy price - the cheaper of importing
    from Jita (landed at C-J, same formula as an ore's) or buying it right at
    the C-J home market (no haul needed) - or None when neither market has it
    listed right now, in which case the optimizer can only source it by
    refining ore. `source` is "Jita" or "Home", matching whichever price won;
    None when landed_cost_per_unit is None too."""
    type_id: int
    name: str
    landed_cost_per_unit: Optional[float]
    source: Optional[str] = None


@dataclass
class OrePurchase:
    """One line of the final shopping list: buy `units` (= `portions` whole
    portions) of this ore/ice type."""
    type_id: int
    item: str
    family: str
    is_ice: bool
    portions: int
    units: int
    volume_m3: float          # total haul volume for `units`
    landed_cost_per_unit: float
    total_cost: float


@dataclass
class DirectMineralPurchase:
    """One line of the final shopping list: buy this mineral outright rather
    than refining it out of ore. `source` is "Jita" or "Home" - see
    MineralOption's own docstring."""
    type_id: int
    name: str
    quantity: int
    landed_cost_per_unit: float
    total_cost: float
    source: Optional[str] = None


@dataclass
class MineralCoverage:
    """Per-mineral proof the plan actually clears the requirement - `from_ore`
    is what the rounded-up ore purchases really deliver (not the LP's
    continuous figure), so `surplus` is the genuine leftover."""
    type_id: int
    name: str
    required: float
    from_ore: int
    from_direct: int
    delivered: int
    surplus: float


@dataclass
class ShoppingListPlan:
    """The optimizer's full answer - see optimizer.py's optimize_shopping_list."""
    ore_purchases: list[OrePurchase]
    direct_purchases: list[DirectMineralPurchase]
    coverage: list[MineralCoverage]
    ore_cost: float
    direct_cost: float
    total_cost: float
    lp_cost: float                       # the continuous LP optimum, before whole-portion rounding
    all_direct_cost: Optional[float]     # baseline: buy every required mineral outright, no refining
    savings_vs_all_direct: Optional[float]
    total_volume_m3: float

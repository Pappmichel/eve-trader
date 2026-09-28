"""Plain dataclasses for the Module Reprocessing Import tool - mirrors
eve_trader/refining/models.py's OreCandidate/OreShortlistRow shape, minus
the family/is_ice fields (no ore/ice-family skill concept applies here - see
this tool's own module_reprocessing/engine.py, which uses the scrapmetal
yield formula only).

The Mineral Shopping List's types (bottom of this file) are the one place
this tool deals with ore/ice at all: its optimizer mixes compressed ore/ice
and modules/drones as reprocessing sources in one plan, so ReprocessOption/
ReprocessPurchase carry a `category` plus ore-only `family`/`is_ice` fields.
They deliberately duplicate refining/models.py's own shopping-list shapes
rather than importing them - small per-tool duplication of data shapes over
cross-tool data-model imports, same as the rest of this codebase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModuleCandidate:
    """One T1/Meta module or drone type from the SDE category/meta-group
    filter (see module_reprocessing/candidate_discovery.py) - a member of
    this tool's full candidate universe, not yet on anyone's shortlist."""
    type_id: int
    item: str
    volume_m3: float


@dataclass
class DiscoveredModuleResult:
    """One row of a Discover run's output - a candidate plus a cheap,
    Goonmetrics-history-based profitability estimate (see CLAUDE.md's Price
    Sources Matrix: "is this newly discovered candidate historically worth
    importing" is answered from region-average history, never from live
    ESI order-book stats - that's reserved for the shortlist's own Refresh).
    Purely informational - the user picks which rows to add to the real,
    live-priced shortlist; nothing here is persisted."""
    type_id: int
    item: str
    volume_m3: float
    est_landed_cost: Optional[float]
    est_mineral_value: Optional[float]
    est_profit_per_unit: Optional[float]
    est_margin: Optional[float]


@dataclass
class ModuleShortlistRow:
    """Fully computed row - mirrors eve_trader/refining/models.py's
    OreShortlistRow shape (minus family/is_ice)."""
    item_id: int
    item: str
    active: bool
    volume_m3: Optional[float]
    landed_cost: Optional[float]
    yield_pct: Optional[float]
    mineral_value: Optional[float]
    refining_tax: Optional[float]
    net_sell: Optional[float]
    sell_listed_qty: Optional[float]
    profit_per_unit: Optional[float]
    margin: Optional[float]
    profit_per_m3: Optional[float]
    decision: str


# ------------------------------------------------- Mineral Shopping List
# Same feature as Ore & Minerals' own Mineral Shopping List (GitHub issue
# #93, refining/models.py), generalized so one plan can source minerals from
# compressed ore/ice AND T1/Meta modules/drones - see shopping_optimizer.py.
@dataclass
class MineralRequirement:
    """One "I need this many units of this mineral" line - the optimizer's
    right-hand side. Persisted per-tenant in module_reprocessing_mineral_
    requirements (see storage.replace_module_shopping_requirements) -
    deliberately a separate list from Ore & Minerals' own mineral_requirements."""
    type_id: int
    name: str
    required_qty: float


ORE_CATEGORY = "ore"
MODULE_CATEGORY = "module"


@dataclass
class ReprocessOption:
    """One buyable reprocessing source - a compressed ore/ice type
    (`category` "ore") or a T1/Meta module/drone (`category` "module") - fully
    priced and pre-reprocessed into a per-whole-portion mineral yield: one
    column of the LP's constraint matrix. Generalizes refining/models.py's
    OreOption. `yield_per_portion` is what ONE whole portion (portion_size
    units - 100 for most ore, 1 for most modules) actually reprocesses into at
    this tenant's configured yield%, already net of the structure's
    reprocessing tax and already floored per material the way EVE itself
    rounds - the LP never re-derives any of that. `family`/`is_ice` are only
    meaningful for ore (None/False for a module)."""
    type_id: int
    item: str
    category: str
    family: Optional[str]
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
    from Jita (landed at C-J) or buying it right at the C-J home market - or
    None when neither market has it listed right now, in which case the
    optimizer can only source it by reprocessing. `source` is "Jita" or
    "Home", matching whichever price won; None when landed_cost_per_unit is
    None too. Same shape as refining/models.py's MineralOption."""
    type_id: int
    name: str
    landed_cost_per_unit: Optional[float]
    source: Optional[str] = None


@dataclass
class ReprocessPurchase:
    """One line of the final shopping list: buy `units` (= `portions` whole
    portions) of this ore/ice type or module/drone and reprocess it."""
    type_id: int
    item: str
    category: str
    family: Optional[str]
    is_ice: bool
    portions: int
    units: int
    volume_m3: float          # total haul volume for `units`
    landed_cost_per_unit: float
    total_cost: float


@dataclass
class DirectMineralPurchase:
    """One line of the final shopping list: buy this mineral outright rather
    than reprocessing it out of anything. `source` is "Jita" or "Home"."""
    type_id: int
    name: str
    quantity: int
    landed_cost_per_unit: float
    total_cost: float
    source: Optional[str] = None


@dataclass
class MineralCoverage:
    """Per-mineral proof the plan actually clears the requirement -
    `from_reprocessing` is what the whole-portion ore AND module purchases
    really deliver together, so `surplus` is the genuine leftover."""
    type_id: int
    name: str
    required: float
    from_reprocessing: int
    from_direct: int
    delivered: int
    surplus: float


@dataclass
class ModuleShoppingListPlan:
    """The optimizer's full answer - see shopping_optimizer.py's
    optimize_shopping_list. `reprocess_purchases` holds both ore and module
    lines (tell them apart by `category`); `reprocess_cost` is their sum."""
    reprocess_purchases: list[ReprocessPurchase]
    direct_purchases: list[DirectMineralPurchase]
    coverage: list[MineralCoverage]
    reprocess_cost: float
    direct_cost: float
    total_cost: float
    lp_cost: float                       # the continuous LP optimum, before whole-portion rounding
    all_direct_cost: Optional[float]     # baseline: buy every required mineral outright, no reprocessing
    savings_vs_all_direct: Optional[float]
    total_volume_m3: float

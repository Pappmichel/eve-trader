"""Plain dataclasses for the Module Reprocessing Import tool - mirrors
eve_trader/refining/models.py's OreCandidate/OreShortlistRow shape, minus
the family/is_ice fields (no ore/ice-family skill concept applies here - see
this tool's own module_reprocessing/engine.py, which uses the scrapmetal
yield formula only).
"""
from __future__ import annotations

from dataclasses import dataclass
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

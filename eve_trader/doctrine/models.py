"""Dataclasses for the Doctrine tool - mirrors production/models.py's own
shape (plain dataclasses, no framework imports; api/schemas.py mirrors these
1:1 as Pydantic models for the API layer, see that module's own docstring).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------- parser output
# Returned by doctrine/parser.py - deliberately *without* fitting_id (no
# Fitting row exists yet at parse time, see parser.py's own docstring on its
# storage-free contract). do_add_fitting (actions.py) attaches fitting_id
# when persisting these into the real FittingItem/ParseIssue rows below.
@dataclass
class ParsedItem:
    line_no: int
    slot_section: str          # one of constants.SLOT_SECTIONS
    type_id: int
    quantity: float
    is_offline: bool = False


@dataclass
class ParsedIssue:
    line_no: int
    raw_line: str
    issue_kind: str             # one of constants.ISSUE_KINDS
    message: str


@dataclass
class ParsedFitting:
    """Full result of parser.parse_fitting - do_parse_fitting (preview)
    returns this as-is; do_add_fitting persists it."""
    hull_type_id: int
    hull_name: str
    fit_name: str
    items: list[ParsedItem] = field(default_factory=list)
    issues: list[ParsedIssue] = field(default_factory=list)


# --------------------------------------------------------------- master data
@dataclass
class Doctrine:
    doctrine_id: str
    name: str
    description: Optional[str] = None
    active: bool = True
    created_at: Optional[str] = None


@dataclass
class Fitting:
    fitting_id: str
    doctrine_id: str
    name: str
    hull_type_id: int
    raw_eft: str
    variant_label: Optional[str] = None
    contract_target: int = 0
    stockpile_target: int = 0
    cargo_tolerance_pct: Optional[float] = None
    active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class FittingItem:
    fitting_id: str
    line_no: int
    slot_section: str
    type_id: int
    quantity: float
    is_offline: bool = False


@dataclass
class ParseIssue:
    fitting_id: str
    line_no: int
    raw_line: str
    issue_kind: str
    message: str


# --------------------------------------------------------------- sync / results
@dataclass
class ContractRow:
    contract_id: int
    source_role: str
    for_corporation: bool
    status: str
    validation_status: str
    issuer_id: Optional[int] = None
    start_location_id: Optional[int] = None
    title: Optional[str] = None
    price: Optional[float] = None
    date_expired: Optional[str] = None
    matched_fitting_id: Optional[str] = None
    match_score: Optional[float] = None
    synced_at: Optional[str] = None
    # Enriched by actions.do_list_contracts (not stored, not set by
    # contract_rows_from_db) - source_role ("doctrine:<character_id>", an
    # ESI-token role key) resolved to the character's actual name where
    # known; hull resolved via matched_fitting_id (None for an unmatched
    # contract - we don't authoritatively know which hull it's for then).
    source_character_name: Optional[str] = None
    hull_type_id: Optional[int] = None
    hull_name: Optional[str] = None


@dataclass
class ContractHistoryRow:
    """GitHub issue #19 - a permanent record of a finished (accepted) Doctrine
    contract sale, independent of doctrine_contracts' own active/wholesale-
    replaced snapshot (see storage.upsert_doctrine_contract_history's own
    docstring for why). fitting_name/hull_type_id are a denormalized
    snapshot captured when the contract finished, not a live join - stays
    meaningful even if that fitting is later edited/deactivated/deleted.
    hull_name is resolved at read time (SDE type names are stable reference
    data, not something a tenant edits/deletes the way their own fittings
    are - see actions.do_contract_history)."""
    contract_id: int
    source_role: str
    fitting_id: Optional[str] = None
    fitting_name: Optional[str] = None
    hull_type_id: Optional[int] = None
    hull_name: Optional[str] = None
    title: Optional[str] = None
    price: Optional[float] = None
    acceptor_id: Optional[int] = None
    acceptor_name: Optional[str] = None
    date_issued: Optional[str] = None
    date_completed: Optional[str] = None
    source_character_name: Optional[str] = None


@dataclass
class ContractItemRow:
    contract_id: int
    record_id: int
    type_id: int
    quantity: float
    is_included: bool = True
    is_singleton: bool = False


@dataclass
class DeviationRow:
    contract_id: int
    type_id: int
    kind: str
    severity: str
    expected_qty: float = 0.0
    actual_qty: float = 0.0


@dataclass
class StockpileRow:
    """Not persisted - a live response model (Phase 2 B.2). One row per
    (fitting_id, type_id) - see engine.aggregate_stockpile_rows for the
    combined-across-fittings/doctrines summary the Stockpile page also
    shows."""
    fitting_id: str
    fitting_name: str
    doctrine_id: str
    doctrine_name: str
    type_id: int
    type_name: str
    slot_section: str
    required_total: float
    available: float
    shortfall: float
    severity: Optional[str]   # None when shortfall == 0 (no deviation at all)


@dataclass
class AggregatedStockpileRow:
    """engine.aggregate_stockpile_rows' output - the same item required by
    several fittings/doctrines summed into one row, so "how much of X do I
    need in total" doesn't require manually adding up several StockpileRows
    (GitHub issue #1). Not persisted, same as StockpileRow."""
    type_id: int
    type_name: str
    required_total: float
    available: float
    shortfall: float
    severity: Optional[str]
    fitting_count: int


@dataclass
class ShoppingListRow:
    """engine.shopping_list_rows' output - one row per type_id with a real
    stockpile shortfall (across every doctrine), enriched with a Build-vs-
    Buy-C-J-vs-Buy-Jita comparison. build_cost/cj_price/jita_landed_price
    are always all populated when available, regardless of which one wins -
    a "Build"-recommended row still shows exactly where it's cheapest to buy
    instead, if you'd rather buy than build it yourself."""
    type_id: int
    type_name: str
    shortfall: float
    build_cost: Optional[float]         # per unit
    cj_price: Optional[float]           # per unit
    jita_landed_price: Optional[float]  # per unit, includes DoctrineConfig.import_cost_per_m3
    recommended_source: Optional[str]   # "Build" | "C-J" | "Jita" | None (no price data at all)
    total_cost: Optional[float]         # recommended price * shortfall


# --------------------------------------------------------------- status / ampel
@dataclass
class FittingStatus:
    fitting_id: str
    fitting_name: str
    doctrine_id: str
    contract_status: str            # ampel
    valid_contracts: int
    tolerable_contracts: int
    contract_target: int
    stockpile_status: str           # ampel
    stockpile_target: int
    worst_stockpile_shortfall_pct: float
    last_synced_at: Optional[str]
    assets_available: bool = True
    hull_type_id: int = 0
    hull_name: str = ""
    # Cost of buying the whole fit (hull + every item) at the home market
    # right now, raw sell price with no broker fee (multibuy instantly fills
    # existing sell orders, unlike engine._shopping_prices' own Buy-C-J
    # price which models placing your own order) - see engine.fitting_status.
    # None when the caller didn't supply live home prices, or any item's
    # price is missing (a partial total would be misleading).
    multibuy_cost: Optional[float] = None


@dataclass
class DoctrineStatus:
    doctrine_id: str
    doctrine_name: str
    overall: str                    # ampel
    contract_rollup: str            # ampel
    stockpile_rollup: str           # ampel
    fittings: list[FittingStatus] = field(default_factory=list)

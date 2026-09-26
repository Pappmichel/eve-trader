"""Reprocessing tab quote calculation - GitHub issue #92. Uses the
scrapmetal path (refining/engine.py's scrapmetal_yield - structure/rig/
implant/general Reprocessing skills have no effect, only RefiningConfig.
scrapmetal_processing_skill_level) for a non-ore/ice item, the primary use
case this tab was originally built for (modules/ammo/drones/loot) - but an
ore/ice paste line (raw or compressed - see candidate_discovery.py's
ore_ice_family_for_type) gets the real ore_ice_yield formula instead
(T2-01, business-logic audit, 2026-09-25/26: this tab used to apply
scrapmetal_yield to ore/ice too, silently understating its real yield -
up to ~90.6%, vs scrapmetal's ~55% cap - with no warning at all).

    Sell-as-is value = quantity x C-J sell percentile x structure_sell_haircut
    Refined value     = mineral yield (scrapmetal_yield or ore_ice_yield,
                         whichever applies, portion-size-rounded)
                         x mineral C-J sell percentile x structure_sell_haircut - Refining Tax
    Recommendation    = "Reprocess" if Refined value > Sell-as-is value, else "Sell instead"
                         (both numbers always shown - nothing is auto-decided/excluded, #92's
                         own "informational, not auto-decided" requirement)

Both options end with a C-J sell order, so both incur the same
structure_sell_haircut (broker fee + sales tax + SCC surcharge, ~5.37%) -
confirmed real bug (found in a business-logic audit, 2026-08-29): sell-as-is
used to be a bare gross quantity x price with no haircut at all, while the
refined side already netted it out on the mineral side. That understated
reprocessing's relative value by the same ~5.37%, systematically biasing
borderline items toward "Sell instead" even when reprocessing was actually
the better outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from .. import storage
from ..config import TRADING_CONFIG, TradingConfig
from ..esi_client import OrderStats
from .config import REFINING_CONFIG, RefiningConfig
from .engine import apply_reprocessing_yield, ore_ice_yield, scrapmetal_yield
from ..paste_parser import ParsedPasteLine

REPROCESS_DECISION = "Reprocess"
SELL_DECISION = "Sell instead"
NOT_REPROCESSABLE_DECISION = "Not reprocessable"
UNRESOLVED_DECISION = "Unknown item"
NO_MARKET_DATA_DECISION = "No market data"

# Sentinel so evaluate_reprocessing_line can tell "caller didn't resolve"
# from "caller resolved, item unknown" (None).
_TYPE_ID_UNRESOLVED = object()


@dataclass
class ReprocessingQuoteRow:
    name: str
    quantity: int
    type_id: Optional[int]
    category: str
    sell_as_is_value: Optional[float]
    refined_value: Optional[float]
    mineral_value: Optional[float]
    refining_tax: Optional[float]
    decision: str
    error: Optional[str] = None


def resolve_type_id(name: str) -> Optional[int]:
    """Exact (case-insensitive) match against the SDE - a paste line's own
    name field should already be a real EVE item name, so a fuzzy/substring
    match (like search_sde_types' own type-ahead ordering) risks silently
    resolving to the wrong item; better to report "unknown" and let the user
    fix a typo than guess."""
    candidates = storage.search_sde_types(name, limit=5)
    for type_id, type_name in candidates:
        if type_name.strip().lower() == name.strip().lower():
            return type_id
    return None


def mineral_type_ids_for_lines(type_ids: list[int]) -> list[int]:
    ids: set[int] = set()
    for materials in storage.get_type_materials_bulk(type_ids).values():
        for material_type_id, _qty in materials:
            ids.add(material_type_id)
    return sorted(ids)


def evaluate_reprocessing_line(line: ParsedPasteLine, item_stats: Optional[OrderStats],
                                mineral_stats_by_id: dict[int, OrderStats],
                                trading_cfg: TradingConfig = TRADING_CONFIG,
                                refining_cfg: RefiningConfig = REFINING_CONFIG,
                                type_id: Union[int, None, object] = _TYPE_ID_UNRESOLVED,
                                ore_ice_family: Optional[tuple[str, bool]] = None) -> ReprocessingQuoteRow:
    """Pure - `item_stats`/`mineral_stats_by_id`/`ore_ice_family` are pre-
    fetched by the caller (see do_quote_reprocessing), no network calls
    here. `type_id` is optional: do_quote_reprocessing passes the already-
    resolved id so each paste name is looked up once; unit tests that omit
    it still resolve here. `ore_ice_family` is `(family, is_ice)` when
    `type_id` is ore/ice (see candidate_discovery.ore_ice_families_for_
    types), None otherwise - selects ore_ice_yield vs scrapmetal_yield
    below (T2-01)."""
    if line.error:
        return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=None, category=line.category,
                                     sell_as_is_value=None, refined_value=None, mineral_value=None,
                                     refining_tax=None, decision=UNRESOLVED_DECISION, error=line.error)

    if type_id is _TYPE_ID_UNRESOLVED:
        type_id = resolve_type_id(line.name)
    if type_id is None:
        return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=None, category=line.category,
                                     sell_as_is_value=None, refined_value=None, mineral_value=None,
                                     refining_tax=None, decision=UNRESOLVED_DECISION,
                                     error="No exact match in the SDE for this item name.")

    portion_size = storage.get_portion_size(type_id)
    materials = storage.get_type_materials(type_id)
    sell_as_is_value = (
        item_stats.sell_percentile * line.quantity * trading_cfg.structure_sell_haircut
        if item_stats and item_stats.sell_percentile is not None else None
    )

    if not portion_size or not materials:
        return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=type_id, category=line.category,
                                     sell_as_is_value=sell_as_is_value, refined_value=None, mineral_value=None,
                                     refining_tax=None, decision=NOT_REPROCESSABLE_DECISION)

    if ore_ice_family is not None:
        family, _is_ice = ore_ice_family
        yield_pct = ore_ice_yield(refining_cfg, family)
    else:
        yield_pct = scrapmetal_yield(refining_cfg)
    minerals = apply_reprocessing_yield(type_id, line.quantity, yield_pct)

    mineral_value = 0.0
    have_full_mineral_data = bool(minerals)
    for material_type_id, qty in minerals.items():
        stats = mineral_stats_by_id.get(material_type_id)
        if stats is None or stats.sell_percentile is None:
            have_full_mineral_data = False
            continue
        mineral_value += qty * stats.sell_percentile * trading_cfg.structure_sell_haircut

    if sell_as_is_value is None or not have_full_mineral_data:
        return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=type_id, category=line.category,
                                     sell_as_is_value=sell_as_is_value, refined_value=None,
                                     mineral_value=mineral_value if have_full_mineral_data else None,
                                     refining_tax=None, decision=NO_MARKET_DATA_DECISION)

    refining_tax = mineral_value * refining_cfg.refining_tax_rate
    refined_value = mineral_value - refining_tax
    decision = REPROCESS_DECISION if refined_value > sell_as_is_value else SELL_DECISION

    return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=type_id, category=line.category,
                                 sell_as_is_value=sell_as_is_value, refined_value=refined_value,
                                 mineral_value=mineral_value, refining_tax=refining_tax, decision=decision)

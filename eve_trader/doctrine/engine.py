"""Doctrine tool orchestration - the only doctrine/*.py module that touches
storage.py directly (Phase 2 A.1: parser.py/validation.py stay pure).
Contract<->Fitting matching, status/ampel aggregation, and Stockpile Soll/Ist
all live here, on top of storage.py's plain-tuple reads and validation.py's
pure scoring/deviation functions.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .. import hubs, storage
from ..config import TRADING_CONFIG
from ..esi_client import ESIClient, ESIError, OrderStats
from ..esi_data.access import read_esi
from . import buyer_sources, equivalents, parser, validation
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import AMPEL_GRAY, EXACT_SECTIONS
from .models import (
    AggregatedStockpileRow, ContractItemRow, ContractRow, DeviationRow, Doctrine, DoctrineStatus, Fitting,
    FittingItem, FittingStatus, ParsedFitting, ParsedIssue, ParsedItem, ParseIssue, ShoppingListRow, StockpileRow,
)
from .parser import FittingParseError, ResolvedType

log = logging.getLogger("eve_trader.doctrine.engine")

# Deliberate, confirmed exception to constants.py's own "no imports from
# eve_trader.production" policy (see that module's docstring) - the
# Shopping List's Build cost (shopping_list_rows below) reuses Production's
# real build-cost engine directly rather than a second, possibly-divergent
# estimate, so a user's owned-BPO/ME/TE/structure config gives the exact
# same number here as it would in the Production tool itself. Only pure,
# stateless price/cost computation is imported - no shared mutable state or
# tables, so this doesn't reintroduce the kind of coupling that policy was
# actually protecting against.
from ..production.config import PRODUCTION_CONFIG
from ..production import pricing as production_pricing
from ..production.engine import _PlanContext, _haul_volume, classify_activity, unit_cost_detail


# ---------------------------------------------------------------- parsing entry point
def _resolve_name(name: str) -> Optional[ResolvedType]:
    row = storage.resolve_sde_type_by_name(name)
    if row is None:
        return None
    type_id, group_id, category_id, meta_group_id, meta_level, type_name = row
    return ResolvedType(type_id=type_id, group_id=group_id, category_id=category_id,
                         meta_group_id=meta_group_id, meta_level=meta_level, type_name=type_name)


def _resolve_slot(type_id: int) -> Optional[str]:
    return storage.get_type_slot(type_id)


def parse_fitting_text(raw_eft: str) -> ParsedFitting:
    """The one place doctrine/parser.py's storage-free resolvers get wired
    up to real SDE data - see parser.parse_fitting's own docstring for why
    the parser itself never imports storage.py."""
    candidates = storage.list_hull_type_names()
    return parser.parse_fitting(raw_eft, _resolve_name, _resolve_slot, hull_name_candidates=candidates)


def parse_bay_items_text(text: str, slot_section: str,
                          line_no_start: int = 1) -> tuple[list[ParsedItem], list[ParsedIssue]]:
    """Same real-SDE wiring as parse_fitting_text above, for parser.
    parse_bay_items (GitHub issue #18 - Fuel Bay / Ship Maintenance Bay
    content lists, entered separately from the main EFT paste since that
    format has no syntax for either)."""
    items, issues = parser.parse_bay_items(text, _resolve_name, slot_section, line_no_start=line_no_start)
    return items, issues


# ---------------------------------------------------------------- row <-> dataclass mapping
def fitting_from_row(row: tuple) -> Fitting:
    (fitting_id, doctrine_id, name, variant_label, hull_type_id, raw_eft, contract_target,
     stockpile_target, cargo_tolerance_pct, active, created_at, updated_at, fuel_bay_text,
     ship_maintenance_bay_text) = row
    return Fitting(fitting_id=str(fitting_id), doctrine_id=str(doctrine_id), name=name,
                    variant_label=variant_label, hull_type_id=hull_type_id, raw_eft=raw_eft,
                    contract_target=contract_target, stockpile_target=stockpile_target,
                    cargo_tolerance_pct=cargo_tolerance_pct, active=active,
                    created_at=str(created_at) if created_at else None,
                    updated_at=str(updated_at) if updated_at else None,
                    fuel_bay_text=fuel_bay_text, ship_maintenance_bay_text=ship_maintenance_bay_text)


def doctrine_from_row(row: tuple) -> Doctrine:
    doctrine_id, name, description, active, created_at = row
    return Doctrine(doctrine_id=str(doctrine_id), name=name, description=description, active=active,
                     created_at=str(created_at) if created_at else None)


def items_from_rows(fitting_id: str, rows: list[tuple]) -> list[FittingItem]:
    return [FittingItem(fitting_id=fitting_id, line_no=line_no, slot_section=slot_section,
                         type_id=type_id, quantity=quantity, is_offline=is_offline)
            for line_no, slot_section, type_id, quantity, is_offline in rows]


def load_fitting_with_items(fitting_id: str) -> tuple[Fitting, list[FittingItem]]:
    row = storage.get_fitting(fitting_id)
    if row is None:
        raise LookupError(f"Fitting {fitting_id} not found.")
    fitting = fitting_from_row(row)
    items = items_from_rows(fitting_id, storage.load_fitting_items(fitting_id))
    return fitting, items


def equivalents_of(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> Optional[validation.EquivalentsOf]:
    """The equivalence lookup Doctrine validation uses (functionally
    identical variants, eve_trader/sde_equivalence.py), or None when
    DoctrineConfig.accept_equivalent_variants is off."""
    return equivalents.lookup() if cfg.accept_equivalent_variants else None


def _with_equivalents(type_ids, lookup: Optional[validation.EquivalentsOf]) -> set[int]:
    """`type_ids` plus every equivalent variant of each."""
    out = set(type_ids)
    if lookup is not None:
        for type_id in list(out):
            out.update(lookup(type_id))
    return out


def effective_cargo_tolerance(fitting: Fitting, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> float:
    return fitting.cargo_tolerance_pct if fitting.cargo_tolerance_pct is not None else cfg.cargo_tolerance_pct


# ---------------------------------------------------------------- matching candidates
@dataclass
class _Candidate:
    fitting: Fitting
    exact_soll: dict[int, float]
    consume_soll: dict[int, float]
    items: list[FittingItem]


def load_match_candidates() -> list[_Candidate]:
    """Every active fitting (Phase 3 B.5 step 1's candidate pool), in
    doctrine-then-fitting creation order - the same order Phase 3 C.2's
    stockpile allocation treats as priority, and the fallback tiebreak for
    Phase 3 B.5 step 3 when neither the title hint nor a strict score
    difference decides a match."""
    candidates = []
    for row in storage.list_active_fittings():
        fitting = fitting_from_row(row)
        items = items_from_rows(fitting.fitting_id, storage.load_fitting_items(fitting.fitting_id))
        exact_soll, consume_soll = validation.build_contract_soll(items)
        candidates.append(_Candidate(fitting=fitting, exact_soll=exact_soll, consume_soll=consume_soll, items=items))
    return candidates


def _fit_identity(candidate: "_Candidate") -> tuple:
    """The fit a candidate represents, independent of which doctrine it sits
    in or how its EFT text was formatted: hull plus the exact/consume Soll
    multisets. Two fittings with the same identity are "the same fit"."""
    return (candidate.fitting.hull_type_id,
            frozenset(candidate.exact_soll.items()),
            frozenset(candidate.consume_soll.items()))


def pooled_contract_counts(candidates: Optional[list["_Candidate"]] = None
                            ) -> dict[str, tuple[int, int]]:
    """Active contracts as one shared pool across doctrines (confirmed with
    the user 2026-10-28): a valid/tolerable contract matched to a fit counts
    toward *every* fitting with the same identity, not only the single
    fitting matching picked. Returns {fitting_id: (valid, tolerable)}.

    A contract is still matched to one fitting at sync time; this only pools
    the *counting* so a shared support ship contracted once is seen by every
    doctrine that fields it. `status == "expired"` never counts (same rule as
    fitting_status/stockpile)."""
    if candidates is None:
        candidates = load_match_candidates()
    identity_by_fitting = {c.fitting.fitting_id: _fit_identity(c) for c in candidates}
    valid_by_identity: dict[tuple, int] = defaultdict(int)
    tolerable_by_identity: dict[tuple, int] = defaultdict(int)
    for ct in contract_rows_from_db(storage.list_doctrine_contracts()):
        if ct.status == "expired" or not ct.matched_fitting_id:
            continue
        key = identity_by_fitting.get(str(ct.matched_fitting_id))
        if key is None:
            continue
        if ct.validation_status == "valid":
            valid_by_identity[key] += 1
        elif ct.validation_status == "tolerable":
            tolerable_by_identity[key] += 1
    return {fid: (valid_by_identity[key], tolerable_by_identity[key])
            for fid, key in identity_by_fitting.items()}


def sold_out_fittings(sold: set[int], unavailable: set[int], now: datetime) -> list[str]:
    """Names of the fittings a sale just emptied (Discord contract alert).
    `sold`: contract ids that just finished; `unavailable`: every contract id
    a live ESI read shows as no longer buyable (finished, deleted, or past its
    expiry), a superset of `sold`. A fitting counts as sold out only when one
    of the sold contracts was a valid/tolerable match for it and no other
    contract of the same fit (the pooled identity of pooled_contract_counts)
    is still available - a fitting that already had no contract never counts.
    The snapshot can lag behind ESI; `unavailable` covers what the live read
    already knows."""
    candidates = load_match_candidates()
    by_id = {c.fitting.fitting_id: c for c in candidates}
    identity_by_fitting = {fid: _fit_identity(c) for fid, c in by_id.items()}
    available: dict[tuple, int] = defaultdict(int)
    sold_fittings: dict[tuple, str] = {}
    for ct in contract_rows_from_db(storage.list_doctrine_contracts()):
        key = identity_by_fitting.get(str(ct.matched_fitting_id)) if ct.matched_fitting_id else None
        if key is None or ct.validation_status not in ("valid", "tolerable"):
            continue
        if ct.contract_id in sold:
            sold_fittings.setdefault(key, by_id[str(ct.matched_fitting_id)].fitting.name)
            continue
        expires = datetime.fromisoformat(ct.date_expired) if ct.date_expired else None
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if ct.status == "expired" or ct.contract_id in unavailable or (expires is not None and expires <= now):
            continue
        available[key] += 1
    return [name for key, name in sold_fittings.items() if not available[key]]


#: Status sentinel, never added to constants.VALIDATION_STATUSES. Matching
#: stores it (score 0.0) so the contract is not re-matched on every read,
#: and actions.do_list_contracts leaves such rows out of the contract list.
#: Distinct from the ordinary "unmatched"
#: status (hull present, but score below threshold - a genuine near-miss
#: worth keeping visible) - this one means no candidate fitting's hull was
#: found in the contract at all, i.e. it isn't a Doctrine ship sale.
NO_HULL_MATCH = "no_relevant_hull"


def match_and_validate_contract(contract_id: int, contract_title: Optional[str],
                                 contract_items: list[ContractItemRow], candidates: list[_Candidate],
                                 cfg: DoctrineConfig = DOCTRINE_CONFIG) -> tuple[Optional[str], float, list[DeviationRow], str]:
    """Phase 3 spec B.5 (matching) + B.4/B.6 (deviations) + B.7 (status),
    combined into the one call esi_sync.py needs per contract. Returns
    (matched_fitting_id, match_score, deviations, validation_status) -
    validation_status can be NO_HULL_MATCH (see its own docstring), never
    persisted as such."""
    lookup = equivalents_of(cfg)
    scored: list[tuple[_Candidate, float]] = []
    for c in candidates:
        if not validation.hull_gate_satisfied(contract_items, c.fitting.hull_type_id):
            continue
        ist = validation.build_contract_ist(contract_items, c.fitting.hull_type_id)
        ist, _subs = validation.substitute_equivalents(c.exact_soll, c.consume_soll, ist, lookup)
        score = validation.match_score(c.exact_soll, c.consume_soll, ist)
        scored.append((c, score))

    if not scored:
        return None, 0.0, [], NO_HULL_MATCH

    max_score = max(s for _c, s in scored)
    if not validation.clears_match_threshold(max_score):
        return None, max_score, [], "unmatched"

    tied = [c for c, s in scored if s == max_score]
    winner = tied[0]
    if len(tied) > 1 and contract_title:
        title_matches = [c for c in tied if c.fitting.name.lower() in contract_title.lower()]
        if len(title_matches) == 1:
            winner = title_matches[0]

    tolerance = effective_cargo_tolerance(winner.fitting, cfg)
    deviations = validation.compute_deviations(contract_id, winner.exact_soll, winner.consume_soll,
                                                contract_items, winner.fitting.hull_type_id,
                                                tolerance, cfg.strict_extras, equivalents_of=lookup)
    deviations = validation.pair_wrong_variants(deviations, _group_id_of, _slot_of)
    status = validation.contract_status(deviations, matched=True)
    return winner.fitting.fitting_id, max_score, deviations, status


def _group_id_of(type_id: int) -> Optional[int]:
    row = storage.get_sde_type(type_id)
    return row[1] if row else None


def _slot_of(type_id: int) -> Optional[str]:
    return storage.get_type_slot(type_id)


# ---------------------------------------------------------------- stockpile
def stockpile_rows_for_doctrine(doctrine_id: Optional[str] = None,
                                 cfg: DoctrineConfig = DOCTRINE_CONFIG,
                                 pooled_contracts: Optional[dict[str, tuple[int, int]]] = None,
                                 location_id: Optional[int] = None,
                                 ) -> tuple[list[StockpileRow], bool]:
    """Phase 3 spec C - returns (rows, assets_available). Only ever computed
    live (never persisted, Phase 2 B.2's own note on StockpileRow).
    Asset Ist is the rows `read_esi("assets", "doctrine")` returns, filtered
    with the same location / hangar / intake rules Production uses — never
    an unfiltered read of the shared tables.

    `location_id` (the Stockpile page's structure filter) counts only that
    stockpile location; Soll stays the tenant-wide need either way.
    """
    rows, assets_available, _available = _stockpile_rows(doctrine_id, cfg, pooled_contracts,
                                                          location_id=location_id)
    return rows, assets_available


def stockpile_available(asset_rows: list[dict], type_ids, cfg: DoctrineConfig = DOCTRINE_CONFIG,
                        location_id: Optional[int] = None) -> dict[int, float]:
    """Raw stockpile stock per type (before any priority allocation): the
    asset rows filtered to the stockpile locations, hangar divisions and
    without Sorting intake stacks. The one filter path for Stockpile and
    Max assemblable. Summed over every stockpile location
    (cfg.effective_stockpile_location_ids), or only `location_id` when given;
    no location configured at all counts every location."""
    type_ids = list(type_ids)
    locations: tuple[Optional[int], ...] = (
        (location_id,) if location_id is not None else cfg.effective_stockpile_location_ids) or (None,)
    totals: dict[int, float] = {t: 0.0 for t in type_ids}
    for loc in locations:
        at_loc = storage.esi_stock_from_asset_rows(
            asset_rows, type_ids, loc,
            allowed_flags=cfg.stockpile_hangar_flags,
            exclude_intake_at_location_id=PRODUCTION_CONFIG.home_location_id)
        for type_id, qty in at_loc.items():
            totals[type_id] = totals.get(type_id, 0.0) + qty
    return totals


def _stockpile_rows(doctrine_id: Optional[str], cfg: DoctrineConfig,
                    pooled_contracts: Optional[dict[str, tuple[int, int]]],
                    location_id: Optional[int] = None,
                    ) -> tuple[list[StockpileRow], bool, dict[int, float]]:
    """stockpile_rows_for_doctrine plus the raw stock it read, so callers
    that also need unallocated stock (Max assemblable) do not read twice."""
    asset_rows = read_esi("assets", "doctrine")
    assets_available = bool(asset_rows)

    candidates = load_match_candidates()
    # Pool active contracts across all fittings (tenant-wide) before the
    # doctrine filter, so a shared fit's contracts count for this doctrine too.
    if pooled_contracts is None:
        pooled_contracts = pooled_contract_counts(candidates)
    if doctrine_id is not None:
        candidates = [c for c in candidates if c.fitting.doctrine_id == doctrine_id]
    if not candidates or not assets_available:
        return [], assets_available, {}

    # {doctrine_id: name} - used below so each row carries its doctrine's own
    # name (not a fitting's, which Stockpile.tsx used as a stand-in for the
    # doctrine section heading until this was fixed - GitHub issue #1).
    doctrine_name_by_id = {str(row[0]): row[1] for row in storage.list_doctrines()}

    ordered_soll: list[tuple[str, dict[int, tuple[float, str]]]] = []
    for c in candidates:
        # GitHub issue #36: same valid-contract count fitting_status computes
        # (contract_ampel's own "valid" count) - a fitting still short of its
        # contract_target needs the materials for those extra contracts too,
        # not just its separate stockpile_target buffer. Pooled across
        # identical fits (shared contract pool), same as the contract ampel.
        valid_contracts = pooled_contracts.get(c.fitting.fitting_id, (0, 0))[0]
        soll = validation.build_stockpile_soll(c.items, c.fitting.hull_type_id, c.fitting.stockpile_target,
                                                contract_target=c.fitting.contract_target,
                                                valid_contracts=valid_contracts)
        ordered_soll.append((c.fitting.fitting_id, soll))

    lookup = equivalents_of(cfg)
    type_ids = _with_equivalents({t for _fid, soll in ordered_soll for t in soll}, lookup)
    available_by_type = stockpile_available(asset_rows, type_ids, cfg, location_id=location_id)
    sde_by_id = storage.get_sde_types_bulk(list(type_ids))
    allocation, variants_used = validation.allocate_stockpile_with_equivalents(
        ordered_soll, available_by_type, lookup)

    rows: list[StockpileRow] = []
    for c in candidates:
        soll = dict(next(s for fid, s in ordered_soll if fid == c.fitting.fitting_id))
        alloc = allocation[c.fitting.fitting_id]
        used = variants_used.get(c.fitting.fitting_id, {})
        for type_id, (required, item_class) in soll.items():
            allocated = alloc.get(type_id, 0.0)
            tolerance = effective_cargo_tolerance(c.fitting, cfg)
            shortfall, severity = validation.stockpile_deviation(type_id, required, allocated, item_class, tolerance)
            type_row = sde_by_id.get(type_id)
            type_name = type_row[2] if type_row else str(type_id)
            slot_section = "hull" if type_id == c.fitting.hull_type_id else (
                "low/med/high/rig/subsystem/service" if item_class == "exact" else "drone/cargo/charge")
            rows.append(StockpileRow(
                fitting_id=c.fitting.fitting_id, fitting_name=c.fitting.name,
                doctrine_id=c.fitting.doctrine_id,
                doctrine_name=doctrine_name_by_id.get(c.fitting.doctrine_id, c.fitting.doctrine_id),
                type_id=type_id, type_name=type_name, slot_section=slot_section, required_total=required,
                available=allocated, shortfall=shortfall, severity=severity,
                variant_available=sum(used.get(type_id, {}).values()),
                variant_names=[_name_of(v, sde_by_id) for v in sorted(used.get(type_id, {}))],
            ))
    return rows, assets_available, available_by_type


# ------------------------------------------------------------ max assemblable
def _per_set_soll(candidate: _Candidate) -> dict[int, tuple[float, str]]:
    return validation.build_stockpile_soll(candidate.items, candidate.fitting.hull_type_id, 1)


def _assemblable_summary(candidate: _Candidate, available_by_type: dict[int, float],
                         lookup: Optional[validation.EquivalentsOf] = None) -> tuple[int, Optional[str]]:
    """(max_sets, name of the first bottleneck) for the Doctrine table."""
    max_sets, details = validation.max_assemblable(_per_set_soll(candidate), available_by_type, lookup)
    if not details:
        return 0, None
    bottleneck = min(details, key=lambda t: (details[t]["sets"], t))
    row = storage.get_sde_types_bulk([bottleneck]).get(bottleneck)
    return max_sets, (row[2] if row else str(bottleneck))


def fitting_assemblable(candidate: _Candidate, available_by_type: dict[int, float],
                        all_candidates: list[_Candidate],
                        lookup: Optional[validation.EquivalentsOf] = None) -> dict:
    """How many complete fits of `candidate` the Doctrine stockpile holds
    right now, the items that stop one more and the full requirement table.

    Rules (confirmed with the user 2026-11-04): stockpile only (same filters
    as the Stockpile page), consumables at full quantity, top priority for
    this fitting (stock other fittings would claim still counts). Nothing is
    built or bought; `buildable` only says Production has a recipe, the page
    asks Production's own Max buildable for the real number.

    With `lookup` (accept_equivalent_variants), stock of equivalent variants
    counts too (validation.max_assemblable); each row then carries
    `variant_on_hand` (part of on_hand from variants)."""
    max_sets, details = validation.max_assemblable(_per_set_soll(candidate), available_by_type, lookup)
    sde_by_id = storage.get_sde_types_bulk(list(details))
    hull_id = candidate.fitting.hull_type_id

    needed_by: dict[int, list[str]] = defaultdict(list)
    for other in all_candidates:
        if other.fitting.fitting_id == candidate.fitting.fitting_id:
            continue
        types = set(other.exact_soll) | set(other.consume_soll) | {other.fitting.hull_type_id}
        for type_id in types & set(details):
            if other.fitting.name not in needed_by[type_id]:
                needed_by[type_id].append(other.fitting.name)

    rows = []
    for type_id, d in details.items():
        sde = sde_by_id.get(type_id)
        rows.append({
            "type_id": type_id,
            "type_name": sde[2] if sde else str(type_id),
            "slot_section": "hull" if type_id == hull_id else (
                "low/med/high/rig/subsystem/service" if d["item_class"] == "exact" else "drone/cargo/charge"),
            "item_class": d["item_class"],
            "per_set": d["per_set"],
            "on_hand": d["on_hand"],
            "variant_on_hand": d.get("variant_on_hand", 0.0),
            "sets": d["sets"],
            "short_for_next": d["short_for_next"],
            "buildable": classify_activity(type_id)[1] is not None,
            "also_needed_by": sorted(needed_by.get(type_id, [])),
        })
    rows.sort(key=lambda r: (r["slot_section"] != "hull", r["type_name"].lower()))
    bottlenecks = sorted((r for r in rows if r["short_for_next"] > 1e-9),
                         key=lambda r: (r["sets"], r["type_name"].lower()))
    return {"max_sets": max_sets, "next_sets": max_sets + 1,
            "bottlenecks": bottlenecks, "requirements": rows}


def assemblable_for_fitting(fitting_id: str, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """fitting_assemblable for one stored fitting (an inactive one too),
    reading the stockpile once. Raises LookupError for an unknown id."""
    fitting, items = load_fitting_with_items(fitting_id)
    all_candidates = load_match_candidates()
    candidate = next((c for c in all_candidates if c.fitting.fitting_id == fitting.fitting_id), None)
    if candidate is None:
        exact_soll, consume_soll = validation.build_contract_soll(items)
        candidate = _Candidate(fitting=fitting, exact_soll=exact_soll, consume_soll=consume_soll, items=items)
    asset_rows = read_esi("assets", "doctrine")
    location_ids = list(cfg.effective_stockpile_location_ids)
    names = storage.get_location_names(location_ids) if location_ids else {}
    hull_row = storage.get_sde_type(fitting.hull_type_id)
    result = {
        "fitting_id": fitting.fitting_id,
        "fitting_name": fitting.name,
        "hull_type_id": fitting.hull_type_id,
        "hull_name": hull_row[2] if hull_row else str(fitting.hull_type_id),
        "assets_available": bool(asset_rows),
        "stockpile_locations": [{"location_id": i, "name": names.get(i)} for i in location_ids],
    }
    if not asset_rows:
        return {**result, "max_sets": None, "next_sets": None, "bottlenecks": [], "requirements": []}
    lookup = equivalents_of(cfg)
    available = stockpile_available(asset_rows, _with_equivalents(_per_set_soll(candidate), lookup), cfg)
    return {**result, **fitting_assemblable(candidate, available, all_candidates, lookup)}


def _name_of(type_id: int, sde_by_id: dict) -> str:
    row = sde_by_id.get(type_id)
    return row[2] if row else str(type_id)


def aggregate_stockpile_rows(rows: list[StockpileRow]) -> list[AggregatedStockpileRow]:
    """Combines the same item across every fitting/doctrine that needs it
    into one row (GitHub issue #1) - "how much of X do I need in total"
    without manually adding up several StockpileRows. Summing
    required_total/available/shortfall across rows for the same type_id is
    mathematically safe: validation.allocate_stockpile already partitions
    the physical stock across fittings by priority, so no row's `available`
    double-counts another's. severity is the worst among contributing rows
    (validation.worst_severity) - two fittings can legitimately disagree
    (different cargo_tolerance_pct), so there's no single "correct" tolerance
    to recompute a combined severity from directly."""
    by_type: dict[int, list[StockpileRow]] = {}
    for r in rows:
        by_type.setdefault(r.type_id, []).append(r)

    aggregated = [
        AggregatedStockpileRow(
            type_id=type_id, type_name=group[0].type_name,
            required_total=sum(r.required_total for r in group),
            available=sum(r.available for r in group),
            shortfall=sum(r.shortfall for r in group),
            severity=validation.worst_severity([r.severity for r in group]),
            fitting_count=len(group),
            variant_available=sum(r.variant_available for r in group),
            variant_names=sorted({n for r in group for n in r.variant_names}),
        )
        for type_id, group in by_type.items()
    ]
    aggregated.sort(key=lambda r: r.shortfall, reverse=True)
    return aggregated


def doctrine_needs_by_type() -> dict[int, dict]:
    """{type_id: {"shortfall": units, "doctrines": [names]}} for every item
    the doctrine stockpile is currently short of (the Stockpile page's own
    shortfall, summed over fittings) - the Trading and Production
    Unlisted Stock checks badge these so stock a doctrine needs is not
    listed for sale (user request 2026-10-30).

    Empty while the tenant does not hold the `doctrine` grant (cross-tool
    read, same rule as the Doctrine skill check), when no doctrine assets
    are shared, and on any failure - the badge is a hint and must never
    break the caller's check."""
    from .. import tenant_eligibility

    tenant_id = storage.get_current_tenant()
    if tenant_id is not None and not tenant_eligibility.may_use(
            "doctrine", tenant_eligibility.granted_tools(str(tenant_id))):
        return {}
    try:
        rows, _assets_available = stockpile_rows_for_doctrine()
    except Exception:
        log.warning("doctrine stockpile for the unlisted-stock badge failed", exc_info=True)
        return {}
    needs: dict[int, dict] = {}
    for row in rows:
        if row.shortfall <= 0:
            continue
        need = needs.setdefault(row.type_id, {"shortfall": 0.0, "doctrines": []})
        need["shortfall"] += row.shortfall
        if row.doctrine_name not in need["doctrines"]:
            need["doctrines"].append(row.doctrine_name)
    return needs


# ------------------------------------------------------------- shopping list
def _shopping_prices(type_id: int, home: dict, jita: dict, volume: Optional[float],
                      cfg: DoctrineConfig, home_stats: Optional[OrderStats] = None,
                      jita_stats: Optional[OrderStats] = None,
                      hub_landed: Optional[float] = None) -> tuple[Optional[float], Optional[float]]:
    """(cj_price, jita_landed_price) for the Shopping List's own Buy columns -
    mirrors production.pricing's own buy-candidate formula, but uses
    Doctrine's own cfg.import_cost_per_m3 for the Jita leg instead of
    Production's haul_cost_per_m3 (a deliberately separate, independently-
    configurable value - see DoctrineConfig.import_cost_per_m3's own
    comment). The broker fee is the same real per-character fee either way,
    so that part is still read off PRODUCTION_CONFIG - no reason to
    duplicate it as a second Doctrine setting nobody asked for.

    `home_stats`/`jita_stats` (shopping_list_rows' own live ESI order-book
    check, GitHub issue #10) gate each price on real listed sell volume, not
    just Goonmetrics' quote existing - confirmed real bug: Goonmetrics can
    return a nonzero "sell" price for a market with zero actual sell orders
    right now (confirmed live 2026-08-19 for Federation Navy Iridium Charge
    M at C-J - Goonmetrics' own C-J and Jita quotes for it came back under
    the same "updated" timestamp despite Jita's real ESI order book showing
    over a million units listed and C-J's structure having none). None (not
    an OrderStats) means "couldn't verify" (no seller token / structure
    docking access for the C-J leg) - degrades to trusting the Goonmetrics
    quote as before rather than blocking the page on a fixable auth gap."""
    home_quote, jita_quote = home.get(type_id), jita.get(type_id)
    # T3-04 (2026-09-26): TRADING_CONFIG is the single source of truth for
    # this fee now - ProductionConfig's own former duplicate copy is gone.
    broker_fee = TRADING_CONFIG.jita_buy_broker_fee
    cj = None
    if home_quote and home_quote.sell > 0 and (home_stats is None or home_stats.sell_volume > 0):
        cj = home_quote.sell * (1 + broker_fee)
    jita_landed = None
    if hub_landed is not None:
        # ALL_HUBS mode (#222): hubs.hub_pricing already picked the cheapest
        # landed hub per item from live order books, so use its figure.
        jita_landed = hub_landed if (jita_stats is None or jita_stats.sell_volume > 0) else None
    elif jita_quote and jita_quote.sell > 0 and (jita_stats is None or jita_stats.sell_volume > 0):
        jita_landed = jita_quote.sell * (1 + broker_fee) + cfg.import_cost_per_m3 * (volume or 0)
    return cj, jita_landed


def shopping_list_rows(doctrine_id: Optional[str] = None, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> list[ShoppingListRow]:
    return shopping_list(doctrine_id, cfg)[0]


def shopping_list(doctrine_id: Optional[str] = None,
                  cfg: DoctrineConfig = DOCTRINE_CONFIG) -> tuple[list[ShoppingListRow], list[str]]:
    """(rows, warnings). Every item with a real stockpile shortfall (across every doctrine,
    same aggregation as the Stockpile page's own combined view - GitHub
    issue #1's aggregate_stockpile_rows), each with a Build-vs-Buy-C-J-vs-
    Buy-Jita comparison. Build cost reuses Production's real cost engine
    directly (_PlanContext/unit_cost_detail) - see this module's own import
    comment for why - so it reflects the user's actual owned-BPO/ME/TE/
    structure config, not a second, possibly-divergent estimate.

    With buyer characters configured (DoctrineConfig.buyer_character_ids),
    their hangars and then their open buy orders in the hub region cover the
    shortfall first (buyer_sources.py); `to_buy` is what is left and prices
    `total_cost`. A fully covered row stays in the list with to_buy 0."""
    rows, _ = stockpile_rows_for_doctrine(doctrine_id, cfg)
    aggregated = [r for r in aggregate_stockpile_rows(rows) if r.shortfall > 0]
    if not aggregated:
        return [], []

    # extra_type_ids: _PlanContext's own priced universe is otherwise scoped
    # to Production's stock_targets material closure (see its docstring) -
    # without this, any shortfall item that isn't already a Production stock
    # target (most doctrine ammo/charges) never gets a Goonmetrics quote
    # fetched at all, showing "-" for C-J/Jita even when a real price exists.
    # Same mechanism plan_special_order already relies on for its own
    # never-a-stock-target line items.
    # Equivalent variants (accept_equivalent_variants): every variant is
    # priced too and the row buys the cheapest one.
    lookup = equivalents_of(cfg)
    variants_by_row = {row.type_id: (lookup(row.type_id) if lookup else (row.type_id,)) for row in aggregated}
    type_ids = sorted({t for vs in variants_by_row.values() for t in vs})
    buyer = buyer_sources.collect(type_ids, cfg)
    all_hubs = cfg.hub_region_id == hubs.ALL_HUBS
    # Build-cost material quotes need one real region; ALL_HUBS must never
    # reach an ESI region call as 0 (the per-item best hub comes from
    # hubs.hub_pricing below instead).
    ctx = _PlanContext(PRODUCTION_CONFIG, extra_type_ids=type_ids,
                       hub_region_id=(next(iter(hubs.TRADE_HUBS)) if all_hubs else cfg.hub_region_id))
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, tuple[float, float, Optional[str]]] = {}

    # Live order-book presence check (GitHub issue #10) - fetched once for
    # every shortfall item up front (the structure endpoint has no type_id
    # filter, so a per-item call would re-download the whole order book
    # every time, same reasoning actions.py's own shortlist refresh already
    # documents for structure_order_stats_bulk). Jita is public/no-auth, so
    # it's always attempted; the C-J leg reuses Trading's own "seller" token
    # (esi-markets.structure_markets.v1 isn't a scope Doctrine/Production
    # characters have) since Doctrine's structure defaults to Trading's own
    # sell structure anyway (DoctrineConfig.effective_structure_ids) - best
    # effort, degrades to trusting Goonmetrics for whichever leg it can't
    # verify rather than failing the whole page.
    esi = ESIClient()
    volumes = {t: (_haul_volume(t, PRODUCTION_CONFIG) or 0.0) for t in type_ids}
    try:
        hub_prices = hubs.hub_pricing(esi, cfg.hub_region_id, type_ids, volumes,
                                      TRADING_CONFIG.jita_buy_broker_fee, cfg.import_cost_per_m3)
    except Exception:  # noqa: BLE001 - best-effort; falls back to trusting Goonmetrics for every row
        hub_prices = hubs.HubPricing()
    jita_stats = hub_prices.stats
    home_stats: dict[int, OrderStats] = {}
    # Group 3 ("structure_market_book"), not a "seller:" prefix scan
    # (docs/ESI_ACCESS_PLAN.md Known gap 4 - this call site was missed when
    # the rest of Doctrine moved to sharing/capability lookups, confirmed
    # 2026-09-21): reading a structure's order book needs a character with
    # esi-markets.structure_markets.v1 ticked on the Characters page's
    # Access table, which is an unrelated fact from which token prefix that
    # character happens to be stored under - a character whose only token is
    # producer:<id> or esi:<id> was silently invisible here, so this leg fell
    # back to trusting Goonmetrics even with a perfectly good token
    # available. Iterate-and-try-each, exactly as production/pricing.py's
    # home_prices does for the same structure_order_stats_bulk call.
    # Several contract structures: an item counts as listed when any of them
    # lists it (only sell_volume is read), so keep the stats with the most
    # listed volume per type.
    structure_ids = cfg.effective_structure_ids
    if structure_ids:
        from ..production import esi_sync as production_esi_sync
        capable = production_esi_sync.list_capability_characters("structure_market_book")
        for structure_id in structure_ids:
            for role, _character_id, _name in capable:
                try:
                    stats = esi.structure_order_stats_bulk(structure_id, type_ids, auth_role=role)
                except ESIError:
                    continue  # no docking access for this one - try the next
                for type_id, st in stats.items():
                    if type_id not in home_stats or st.sell_volume > home_stats[type_id].sell_volume:
                        home_stats[type_id] = st
                break

    def _variant_quote(type_id: int) -> dict:
        _, build_cost, _ = unit_cost_detail(type_id, PRODUCTION_CONFIG, ctx.home, ctx.jita, cost_memo,
                                             ctx.selected_decryptors, t2_memo, ctx.cost_indices, ctx.adjusted_prices)
        # _haul_volume (not raw sde_type volume) so ships/capital modules use
        # their packaged volume for the Jita haul leg, not the much larger
        # flight/assembled volume - GitHub issue #11.
        volume = _haul_volume(type_id, PRODUCTION_CONFIG)
        cj_price, jita_landed_price = _shopping_prices(
            type_id, ctx.home, ctx.jita, volume, cfg,
            home_stats.get(type_id), jita_stats.get(type_id),
            hub_landed=hub_prices.landed(type_id, volume or 0.0, TRADING_CONFIG.jita_buy_broker_fee)
            if all_hubs else None)
        candidates = {"Build": build_cost, "C-J": cj_price, "Jita": jita_landed_price}
        priced = {k: v for k, v in candidates.items() if v is not None}
        source = min(priced, key=priced.get) if priced else None
        return {"type_id": type_id, "build": build_cost, "cj": cj_price, "jita": jita_landed_price,
                "source": source, "price": priced[source] if source else None}

    # Buyer hangar / buy-order cover is drawn from one shared pool, so a
    # variant held by a buyer covers at most one shortfall row.
    hangar_left = dict(buyer.hub_hangar) if buyer is not None else {}
    orders_left = dict(buyer.on_order) if buyer is not None else {}

    def _draw(pool: dict, variants: tuple[int, ...], own: int, need: float) -> float:
        got = 0.0
        for t in (own, *[v for v in variants if v != own]):
            take = min(need - got, pool.get(t, 0.0))
            if take > 0:
                pool[t] -= take
                got += take
            if got >= need:
                break
        return got

    result = []
    for row in aggregated:
        variants = variants_by_row[row.type_id]
        quotes = [_variant_quote(t) for t in variants]
        own = next(q for q in quotes if q["type_id"] == row.type_id)
        # Cheapest variant; the fitted type wins ties and is kept when no
        # variant has any price.
        best = min((q for q in quotes if q["price"] is not None),
                   key=lambda q: (q["price"], q["type_id"] != row.type_id), default=own)
        hub_id = hub_prices.hub_by_type.get(best["type_id"]) if best["jita"] is not None else None
        hub_hangar = on_order = 0.0
        if buyer is not None:
            hub_hangar = _draw(hangar_left, variants, row.type_id, row.shortfall)
            on_order = _draw(orders_left, variants, row.type_id, row.shortfall - hub_hangar)
        to_buy = row.shortfall - hub_hangar - on_order
        total_cost = best["price"] * to_buy if best["source"] else None
        swapped = best["type_id"] != row.type_id

        result.append(ShoppingListRow(
            type_id=row.type_id, type_name=row.type_name, shortfall=row.shortfall,
            build_cost=best["build"], cj_price=best["cj"], jita_landed_price=best["jita"],
            recommended_source=best["source"], total_cost=total_cost,
            hub_region_id=hub_id, hub_name=hubs.hub_name(hub_id) if hub_id is not None else None,
            required_total=row.required_total, available=row.available,
            hub_hangar=hub_hangar, on_order=on_order, to_buy=to_buy,
            buy_type_id=best["type_id"] if swapped else None,
            buy_type_name=_type_name(best["type_id"]) if swapped else None,
        ))
    return result, (buyer.warnings if buyer is not None else [])


def _type_name(type_id: int) -> str:
    row = storage.get_sde_type(type_id)
    return row[2] if row else str(type_id)


# ---------------------------------------------------------------- status / ampel
def contract_rows_from_db(rows: list[tuple]) -> list[ContractRow]:
    result = []
    for (contract_id, source_role, for_corporation, issuer_id, start_location_id, status, title, price,
         date_expired, matched_fitting_id, match_score, validation_status, synced_at) in rows:
        result.append(ContractRow(contract_id=contract_id, source_role=source_role,
                                   for_corporation=for_corporation, issuer_id=issuer_id,
                                   start_location_id=start_location_id, status=status, title=title,
                                   price=price, date_expired=str(date_expired) if date_expired else None,
                                   matched_fitting_id=str(matched_fitting_id) if matched_fitting_id else None,
                                   match_score=match_score, validation_status=validation_status,
                                   synced_at=str(synced_at) if synced_at else None))
    return result


def fitting_status(fitting: Fitting, cfg: DoctrineConfig = DOCTRINE_CONFIG,
                    stockpile_rows: Optional[list[StockpileRow]] = None,
                    assets_available: bool = True, home: Optional[dict] = None,
                    pooled_contracts: Optional[dict[str, tuple[int, int]]] = None,
                    available_by_type: Optional[dict[int, float]] = None,
                    candidate: Optional[_Candidate] = None) -> FittingStatus:
    """`home` (Goonmetrics current-price dict, production_pricing.home_prices'
    own return shape) is an optional passthrough for multibuy_cost, same
    "compute once in doctrine_status' loop, don't refetch per fitting"
    reasoning as `stockpile_rows` above - None (the default, e.g. a lone
    do_get_fitting_detail call) just means multibuy_cost comes back None.

    `pooled_contracts` ({fitting_id: (valid, tolerable)}, pooled_contract_counts)
    is the shared active-contract pool: a contract of a shared fit counts for
    every doctrine that fields it. None computes it on demand (standalone
    callers); doctrine_status passes one computed once for the whole pass.
    Expired contracts never count (handled inside pooled_contract_counts).

    `available_by_type` (raw stockpile stock, _stockpile_rows) and
    `candidate` feed the "assemblable now" figure; the standalone path
    reads both itself."""
    if pooled_contracts is None:
        pooled_contracts = pooled_contract_counts()
    last_synced_at = storage.newest_esi_freshness_success_for_tool("doctrine", ("contracts",))
    valid, tolerable = pooled_contracts.get(fitting.fitting_id, (0, 0))
    contract_ampel = validation.contract_ampel(last_synced_at, fitting.contract_target, valid, tolerable)

    if stockpile_rows is None:
        all_rows, assets_available, available_by_type = _stockpile_rows(
            fitting.doctrine_id, cfg, pooled_contracts)
        stockpile_rows = [r for r in all_rows if r.fitting_id == fitting.fitting_id]

    assemblable_now: Optional[int] = None
    assemblable_bottleneck: Optional[str] = None
    if assets_available and available_by_type is not None:
        if candidate is None:
            candidate = next((c for c in load_match_candidates()
                              if c.fitting.fitting_id == fitting.fitting_id), None)
        if candidate is not None:
            assemblable_now, assemblable_bottleneck = _assemblable_summary(
                candidate, available_by_type, equivalents_of(cfg))

    worst_shortfall_pct = 0.0
    worst_severity = None
    for r in stockpile_rows:
        if r.severity is None:
            continue
        pct = (r.shortfall / r.required_total) if r.required_total else 0.0
        worst_shortfall_pct = max(worst_shortfall_pct, pct)
        if r.severity == "critical" or worst_severity is None:
            worst_severity = r.severity
    stockpile_amp = validation.stockpile_ampel(assets_available, fitting.stockpile_target, worst_severity)

    hull_sde = storage.get_sde_type(fitting.hull_type_id)
    hull_name = hull_sde[2] if hull_sde else str(fitting.hull_type_id)

    multibuy_cost = None
    multibuy_missing: list[str] = []
    if home is not None:
        items = storage.load_fitting_items(fitting.fitting_id)
        quantities: dict[int, float] = {fitting.hull_type_id: 1.0}
        for _line_no, _slot_section, type_id, quantity, _is_offline in items:
            quantities[type_id] = quantities.get(type_id, 0.0) + quantity
        total = 0.0
        priced = 0
        for type_id, quantity in quantities.items():
            quote = home.get(type_id)
            if quote is None or quote.sell <= 0:
                if type_id == fitting.hull_type_id:
                    name = hull_name
                else:
                    sde = storage.get_sde_type(type_id)
                    name = sde[2] if sde else str(type_id)
                multibuy_missing.append(f"{quantity:g}x {name}")
                continue
            total += quote.sell * quantity
            priced += 1
        multibuy_cost = total if priced else None

    return FittingStatus(fitting_id=fitting.fitting_id, fitting_name=fitting.name, doctrine_id=fitting.doctrine_id,
                          contract_status=contract_ampel, valid_contracts=valid, tolerable_contracts=tolerable,
                          contract_target=fitting.contract_target, stockpile_status=stockpile_amp,
                          stockpile_target=fitting.stockpile_target,
                          worst_stockpile_shortfall_pct=worst_shortfall_pct, last_synced_at=last_synced_at,
                          assets_available=assets_available, hull_type_id=fitting.hull_type_id,
                          hull_name=hull_name, multibuy_cost=multibuy_cost,
                          multibuy_missing=multibuy_missing, assemblable_now=assemblable_now,
                          assemblable_bottleneck=assemblable_bottleneck)


def doctrine_status(doctrine_row: tuple, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> DoctrineStatus:
    doctrine = doctrine_from_row(doctrine_row)
    fitting_rows = storage.list_fittings_for_doctrine(doctrine.doctrine_id)
    # Shared active-contract pool, computed once tenant-wide for this pass.
    pooled_contracts = pooled_contract_counts()
    stockpile_rows, assets_available, available_by_type = _stockpile_rows(
        doctrine.doctrine_id, cfg, pooled_contracts)
    candidate_by_id = {c.fitting.fitting_id: c for c in load_match_candidates()}

    # Fetched once for every fitting's multibuy_cost below, not per-fitting -
    # matches this function's existing stockpile_rows-computed-once
    # convention. Every hull + fitting item across every active fitting in
    # this doctrine, since pricing.home_prices/jita_prices now require
    # callers to scope type_ids explicitly (see their own docstrings) -
    # these are finished items to price, not materials to recursively
    # expand, so no _structural_material_closure needed here.
    type_ids: set[int] = set()
    for row in fitting_rows:
        fitting = fitting_from_row(row)
        if not fitting.active:
            continue
        type_ids.add(fitting.hull_type_id)
        for _line_no, _slot_section, type_id, _quantity, _is_offline in storage.load_fitting_items(fitting.fitting_id):
            type_ids.add(type_id)
    home = production_pricing.home_prices(PRODUCTION_CONFIG, list(type_ids))

    statuses = []
    for row in fitting_rows:
        fitting = fitting_from_row(row)
        if not fitting.active:
            continue
        own_rows = [r for r in stockpile_rows if r.fitting_id == fitting.fitting_id]
        statuses.append(fitting_status(fitting, cfg, stockpile_rows=own_rows, assets_available=assets_available,
                                        home=home, pooled_contracts=pooled_contracts,
                                        available_by_type=available_by_type,
                                        candidate=candidate_by_id.get(fitting.fitting_id)))

    contract_rollup = validation.worst_ampel([s.contract_status for s in statuses]) if statuses else AMPEL_GRAY
    stockpile_rollup = validation.worst_ampel([s.stockpile_status for s in statuses]) if statuses else AMPEL_GRAY
    overall = validation.worst_ampel([contract_rollup, stockpile_rollup])

    return DoctrineStatus(doctrine_id=doctrine.doctrine_id, doctrine_name=doctrine.name, overall=overall,
                           contract_rollup=contract_rollup, stockpile_rollup=stockpile_rollup, fittings=statuses)

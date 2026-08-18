"""Doctrine tool orchestration - the only doctrine/*.py module that touches
storage.py directly (Phase 2 A.1: parser.py/validation.py stay pure).
Contract<->Fitting matching, status/ampel aggregation, and Stockpile Soll/Ist
all live here, on top of storage.py's plain-tuple reads and validation.py's
pure scoring/deviation functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import storage
from . import parser, validation
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import AMPEL_GRAY, EXACT_SECTIONS
from .models import (
    ContractItemRow, ContractRow, DeviationRow, Doctrine, DoctrineStatus, Fitting, FittingItem,
    FittingStatus, ParsedFitting, ParseIssue, StockpileRow,
)
from .parser import FittingParseError, ResolvedType


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


# ---------------------------------------------------------------- row <-> dataclass mapping
def fitting_from_row(row: tuple) -> Fitting:
    (fitting_id, doctrine_id, name, variant_label, hull_type_id, raw_eft, contract_target,
     stockpile_target, cargo_tolerance_pct, active, created_at, updated_at) = row
    return Fitting(fitting_id=str(fitting_id), doctrine_id=str(doctrine_id), name=name,
                    variant_label=variant_label, hull_type_id=hull_type_id, raw_eft=raw_eft,
                    contract_target=contract_target, stockpile_target=stockpile_target,
                    cargo_tolerance_pct=cargo_tolerance_pct, active=active,
                    created_at=str(created_at) if created_at else None,
                    updated_at=str(updated_at) if updated_at else None)


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


def match_and_validate_contract(contract_id: int, contract_title: Optional[str],
                                 contract_items: list[ContractItemRow], candidates: list[_Candidate],
                                 cfg: DoctrineConfig = DOCTRINE_CONFIG) -> tuple[Optional[str], float, list[DeviationRow], str]:
    """Phase 3 spec B.5 (matching) + B.4/B.6 (deviations) + B.7 (status),
    combined into the one call esi_sync.py needs per contract. Returns
    (matched_fitting_id, match_score, deviations, validation_status)."""
    scored: list[tuple[_Candidate, float]] = []
    for c in candidates:
        if not validation.hull_gate_satisfied(contract_items, c.fitting.hull_type_id):
            continue
        ist = validation.build_contract_ist(contract_items, c.fitting.hull_type_id)
        score = validation.match_score(c.exact_soll, c.consume_soll, ist)
        scored.append((c, score))

    if not scored:
        return None, 0.0, [], "unmatched"

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
                                                tolerance, cfg.strict_extras)
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
                                 cfg: DoctrineConfig = DOCTRINE_CONFIG) -> tuple[list[StockpileRow], bool]:
    """Phase 3 spec C - returns (rows, assets_available). Only ever computed
    live (never persisted, Phase 2 B.2's own note on StockpileRow)."""
    assets_available = storage.has_any_doctrine_synced_assets()
    location_id = cfg.effective_stockpile_location_id

    candidates = load_match_candidates()
    if doctrine_id is not None:
        candidates = [c for c in candidates if c.fitting.doctrine_id == doctrine_id]
    if not candidates or not assets_available:
        return [], assets_available

    ordered_soll: list[tuple[str, dict[int, tuple[float, str]]]] = []
    doctrine_name_by_fitting: dict[str, str] = {}
    for c in candidates:
        soll = validation.build_stockpile_soll(c.items, c.fitting.hull_type_id, c.fitting.stockpile_target)
        ordered_soll.append((c.fitting.fitting_id, soll))

    type_ids = {t for _fid, soll in ordered_soll for t in soll}
    available_by_type = {
        t: storage.esi_stock_at_location(t, location_id, tables=("doctrine_character_assets", "doctrine_corp_assets"))
        for t in type_ids
    }
    allocation = validation.allocate_stockpile(ordered_soll, available_by_type)

    rows: list[StockpileRow] = []
    for c in candidates:
        soll = dict(next(s for fid, s in ordered_soll if fid == c.fitting.fitting_id))
        alloc = allocation[c.fitting.fitting_id]
        for type_id, (required, item_class) in soll.items():
            allocated = alloc.get(type_id, 0.0)
            tolerance = effective_cargo_tolerance(c.fitting, cfg)
            shortfall, severity = validation.stockpile_deviation(type_id, required, allocated, item_class, tolerance)
            type_row = storage.get_sde_type(type_id)
            type_name = type_row[2] if type_row else str(type_id)
            slot_section = "hull" if type_id == c.fitting.hull_type_id else (
                "low/med/high/rig/subsystem/service" if item_class == "exact" else "drone/cargo/charge")
            rows.append(StockpileRow(fitting_id=c.fitting.fitting_id, fitting_name=c.fitting.name,
                                      doctrine_id=c.fitting.doctrine_id, type_id=type_id, type_name=type_name,
                                      slot_section=slot_section, required_total=required, available=allocated,
                                      shortfall=shortfall, severity=severity))
    return rows, assets_available


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
                    assets_available: bool = True) -> FittingStatus:
    contracts = contract_rows_from_db(storage.list_doctrine_contracts(fitting_id=fitting.fitting_id))
    last_synced_at = storage.get_esi_sync_time("doctrine")
    valid = sum(1 for c in contracts if c.validation_status == "valid")
    tolerable = sum(1 for c in contracts if c.validation_status == "tolerable")
    contract_ampel = validation.contract_ampel(last_synced_at, fitting.contract_target, valid, tolerable)

    if stockpile_rows is None:
        all_rows, assets_available = stockpile_rows_for_doctrine(fitting.doctrine_id, cfg)
        stockpile_rows = [r for r in all_rows if r.fitting_id == fitting.fitting_id]

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

    return FittingStatus(fitting_id=fitting.fitting_id, fitting_name=fitting.name, doctrine_id=fitting.doctrine_id,
                          contract_status=contract_ampel, valid_contracts=valid, tolerable_contracts=tolerable,
                          contract_target=fitting.contract_target, stockpile_status=stockpile_amp,
                          worst_stockpile_shortfall_pct=worst_shortfall_pct, last_synced_at=last_synced_at,
                          assets_available=assets_available)


def doctrine_status(doctrine_row: tuple, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> DoctrineStatus:
    doctrine = doctrine_from_row(doctrine_row)
    fitting_rows = storage.list_fittings_for_doctrine(doctrine.doctrine_id)
    stockpile_rows, assets_available = stockpile_rows_for_doctrine(doctrine.doctrine_id, cfg)

    statuses = []
    for row in fitting_rows:
        fitting = fitting_from_row(row)
        if not fitting.active:
            continue
        own_rows = [r for r in stockpile_rows if r.fitting_id == fitting.fitting_id]
        statuses.append(fitting_status(fitting, cfg, stockpile_rows=own_rows, assets_available=assets_available))

    contract_rollup = validation.worst_ampel([s.contract_status for s in statuses]) if statuses else AMPEL_GRAY
    stockpile_rollup = validation.worst_ampel([s.stockpile_status for s in statuses]) if statuses else AMPEL_GRAY
    overall = validation.worst_ampel([contract_rollup, stockpile_rollup])

    return DoctrineStatus(doctrine_id=doctrine.doctrine_id, doctrine_name=doctrine.name, overall=overall,
                           contract_rollup=contract_rollup, stockpile_rollup=stockpile_rollup, fittings=statuses)

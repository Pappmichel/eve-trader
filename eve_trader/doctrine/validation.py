"""Doctrine/Contract + Stockpile validation logic (Phase 3 spec sections B/C)
- pure functions: input is already-loaded FittingItems/ContractItemRows/
asset quantities, output is DeviationRows + status strings. No storage, no
network (Phase 2's own design constraint) - doctrine/engine.py is the only
caller, and it's the one that does the actual storage reads/writes.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable, Optional

from .constants import (
    AMPEL_GRAY, AMPEL_GREEN, AMPEL_RED, AMPEL_SEVERITY_ORDER, AMPEL_YELLOW, CHARGE_CATEGORY_ID,
    CONSUME_SECTIONS, DEVIATION_KIND_EXTRA, DEVIATION_KIND_MISSING, DEVIATION_KIND_SHORT,
    DEVIATION_KIND_WRONG_VARIANT, EXACT_SECTIONS, MATCH_THRESHOLD, MATCH_WEIGHT_CONSUME, MATCH_WEIGHT_EXACT,
    SEVERITY_CRITICAL, SEVERITY_INFO, SEVERITY_TOLERABLE, VALIDATION_INVALID, VALIDATION_TOLERABLE,
    VALIDATION_UNMATCHED, VALIDATION_VALID,
)
from .models import ContractItemRow, DeviationRow, FittingItem

_SEVERITY_RANK = {SEVERITY_CRITICAL: 2, SEVERITY_TOLERABLE: 1, SEVERITY_INFO: 0}


# ---------------------------------------------------------------- Soll construction (B.2/C.1)
def _merge_charges_into_consume(consume: dict[int, float], charge_type_ids: set[int]) -> None:
    """Phase 3 B.2: a charge type_id gets a Soll floor of 1, merged with
    (not added to) any cargo/drone quantity already present for that type."""
    for type_id in charge_type_ids:
        if consume.get(type_id, 0.0) < 1:
            consume[type_id] = 1.0


def build_contract_soll(items: list[FittingItem]) -> tuple[dict[int, float], dict[int, float]]:
    """Returns (exact_soll, consume_soll) for contract validation - the hull
    is deliberately NOT included (B.2: it's the matching gate, checked
    separately by hull_gate_satisfied, not a multiset position). "charge" is
    checked before CONSUME_SECTIONS (GitHub issue #18: that set now also
    includes "fuelbay"/"shipmaintenancebay", which get summed like drone/
    cargo below) since charges get their own floor-of-1 merge semantics
    instead of straight summing - see _merge_charges_into_consume."""
    exact: dict[int, float] = defaultdict(float)
    consume: dict[int, float] = defaultdict(float)
    charge_type_ids: set[int] = set()
    for item in items:
        if item.slot_section in EXACT_SECTIONS:
            exact[item.type_id] += item.quantity
        elif item.slot_section == "charge":
            charge_type_ids.add(item.type_id)
        elif item.slot_section in CONSUME_SECTIONS:
            consume[item.type_id] += item.quantity
    _merge_charges_into_consume(consume, charge_type_ids)
    return dict(exact), dict(consume)


def build_stockpile_soll(items: list[FittingItem], hull_type_id: int,
                          stockpile_target: int) -> dict[int, tuple[float, str]]:
    """Returns {type_id: (required_total, item_class)} for stockpile
    validation - item_class is "exact" or "consume" (drives C.3's severity
    rule). Unlike build_contract_soll, the hull IS a normal position here
    (Phase 2 A.3/C.1: v1 counts type quantities, no fitted-ship detection),
    and everything is multiplied by stockpile_target (C.1)."""
    exact, consume = build_contract_soll(items)
    exact[hull_type_id] = exact.get(hull_type_id, 0.0) + 1.0
    result: dict[int, tuple[float, str]] = {}
    for type_id, qty in exact.items():
        result[type_id] = (qty * stockpile_target, "exact")
    for type_id, qty in consume.items():
        result[type_id] = (qty * stockpile_target, "consume")
    return result


# ---------------------------------------------------------------- Contract matching gate + Ist (B.2/B.3)
def hull_gate_satisfied(contract_items: list[ContractItemRow], hull_type_id: int) -> bool:
    """B.2: the hull must be present and is_included. Deliberately does NOT
    also require is_singleton anymore - confirmed live (2026-08-19) against
    a real contract that a genuinely assembled/fitted ship's own items can
    still come back with is_singleton=False from ESI's contract-items
    endpoint (unlike the asset endpoints, where is_singleton reliably means
    assembled-vs-packaged for ships) - a real fitted Raven with correctly
    slot-labeled modules in the EVE client was wrongly excluded by this
    check. No public ESI signal reliably distinguishes "packaged kit" from
    "assembled ship" at the contract-items level, so this only gates on the
    hull type actually being part of the deal."""
    return any(ci.type_id == hull_type_id and ci.is_included for ci in contract_items)


def build_contract_ist(contract_items: list[ContractItemRow], hull_type_id: int) -> dict[int, float]:
    """B.3: Ist multiset from is_included=True items, hull removed.

    GitHub issue #18's "ignore damaged ammunition": already true by
    construction, nothing to special-case here - ESI's /contracts/{id}/
    items/ response has no damage/durability field at all (confirmed
    against ESI's own contract-item schema: record_id, type_id, quantity,
    raw_quantity, is_blueprint_copy, is_included - the "NN% damaged" label
    is a display-only artifact of the in-game contract window, never part
    of the API data), so a damaged charge already just counts as one more
    unit of its own type_id here, same as an undamaged one - there's
    nothing for this function to filter out."""
    ist: dict[int, float] = defaultdict(float)
    for ci in contract_items:
        if ci.is_included and ci.type_id != hull_type_id:
            ist[ci.type_id] += ci.quantity
    return dict(ist)


# ---------------------------------------------------------------- Deviations (B.4)
def compute_deviations(contract_id: int, exact_soll: dict[int, float], consume_soll: dict[int, float],
                        contract_items: list[ContractItemRow], hull_type_id: int,
                        cargo_tolerance_pct: float, strict_extras: bool = False) -> list[DeviationRow]:
    """B.4's full deviation table, plus B.3's is_included=False "requested
    consideration" extras. Returns at most one DeviationRow per (type_id,
    kind) pair - see this module's own docstring on why exact/consume are
    merged defensively before emitting (a type_id could in principle appear
    in both classes across different fitting lines)."""
    ist = build_contract_ist(contract_items, hull_type_id)
    rows: dict[tuple[int, str], DeviationRow] = {}

    def _add(type_id: int, kind: str, severity: str, expected: float, actual: float) -> None:
        key = (type_id, kind)
        existing = rows.get(key)
        if existing is None or _SEVERITY_RANK[severity] > _SEVERITY_RANK[existing.severity]:
            rows[key] = DeviationRow(contract_id=contract_id, type_id=type_id, kind=kind,
                                      severity=severity, expected_qty=expected, actual_qty=actual)

    all_type_ids = set(exact_soll) | set(consume_soll) | set(ist)
    for type_id in all_type_ids:
        e_exact = exact_soll.get(type_id, 0.0)
        e_consume = consume_soll.get(type_id, 0.0)
        a = ist.get(type_id, 0.0)

        if e_exact > 0:
            if a <= 0:
                _add(type_id, DEVIATION_KIND_MISSING, SEVERITY_CRITICAL, e_exact, 0.0)
            elif a < e_exact:
                _add(type_id, DEVIATION_KIND_SHORT, SEVERITY_CRITICAL, e_exact, a)

        if e_consume > 0:
            threshold = math.ceil(cargo_tolerance_pct * e_consume)
            if a <= 0:
                _add(type_id, DEVIATION_KIND_MISSING, SEVERITY_CRITICAL, e_consume, 0.0)
            elif a < threshold:
                _add(type_id, DEVIATION_KIND_SHORT, SEVERITY_CRITICAL, e_consume, a)
            elif a < e_consume:
                _add(type_id, DEVIATION_KIND_SHORT, SEVERITY_TOLERABLE, e_consume, a)

        if e_exact <= 0 and e_consume <= 0 and a > 0:
            severity = SEVERITY_TOLERABLE if strict_extras else SEVERITY_INFO
            _add(type_id, DEVIATION_KIND_EXTRA, severity, 0.0, a)

    for ci in contract_items:
        if not ci.is_included:
            _add(ci.type_id, DEVIATION_KIND_EXTRA, SEVERITY_INFO, 0.0, ci.quantity)

    return list(rows.values())


def pair_wrong_variants(deviations: list[DeviationRow],
                         group_id_of: Callable[[int], Optional[int]],
                         slot_of: Callable[[int], Optional[str]]) -> list[DeviationRow]:
    """B.6: re-labels a (missing/short, extra) pair as wrong_variant when
    both types share the same group_id AND the same slot classification.
    Greedy, group_id-sorted pairing - a leftover in a group keeps its
    original kind. The expected side stays critical (a different module is
    a different product); the delivered side becomes info."""
    missing_like = [d for d in deviations if d.kind in (DEVIATION_KIND_MISSING, DEVIATION_KIND_SHORT)]
    extras = [d for d in deviations if d.kind == DEVIATION_KIND_EXTRA and d.expected_qty == 0]
    others = [d for d in deviations if d not in missing_like and d not in extras]

    used_extra_ids: set[int] = set()
    result: list[DeviationRow] = []
    for m in sorted(missing_like, key=lambda d: group_id_of(d.type_id) or 0):
        m_group = group_id_of(m.type_id)
        m_slot = slot_of(m.type_id)
        paired = None
        if m_group is not None:
            for e in extras:
                if e.type_id in used_extra_ids:
                    continue
                if group_id_of(e.type_id) == m_group and slot_of(e.type_id) == m_slot:
                    paired = e
                    break
        if paired is not None:
            used_extra_ids.add(paired.type_id)
            result.append(DeviationRow(contract_id=m.contract_id, type_id=m.type_id,
                                        kind=DEVIATION_KIND_WRONG_VARIANT, severity=SEVERITY_CRITICAL,
                                        expected_qty=m.expected_qty, actual_qty=m.actual_qty))
            result.append(DeviationRow(contract_id=paired.contract_id, type_id=paired.type_id,
                                        kind=DEVIATION_KIND_WRONG_VARIANT, severity=SEVERITY_INFO,
                                        expected_qty=paired.expected_qty, actual_qty=paired.actual_qty))
        else:
            result.append(m)
    result.extend(e for e in extras if e.type_id not in used_extra_ids)
    result.extend(others)
    return result


def contract_status(deviations: list[DeviationRow], matched: bool) -> str:
    """B.7."""
    if not matched:
        return VALIDATION_UNMATCHED
    if any(d.severity == SEVERITY_CRITICAL for d in deviations):
        return VALIDATION_INVALID
    if any(d.severity == SEVERITY_TOLERABLE for d in deviations):
        return VALIDATION_TOLERABLE
    return VALIDATION_VALID


# ---------------------------------------------------------------- Matching score (B.5)
def _multiset_overlap(soll: dict[int, float], ist: dict[int, float]) -> float:
    if not soll:
        return 1.0
    total_expected = sum(soll.values())
    if total_expected <= 0:
        return 1.0
    overlap = sum(min(qty, ist.get(type_id, 0.0)) for type_id, qty in soll.items())
    return overlap / total_expected


def match_score(exact_soll: dict[int, float], consume_soll: dict[int, float], ist: dict[int, float]) -> float:
    """B.5 step 2: weighted multiset-overlap score for one fitting candidate
    against one contract's Ist multiset (hull already gated/removed by the
    caller - see hull_gate_satisfied/build_contract_ist)."""
    s_exact = _multiset_overlap(exact_soll, ist)
    s_consume = _multiset_overlap(consume_soll, ist)
    return MATCH_WEIGHT_EXACT * s_exact + MATCH_WEIGHT_CONSUME * s_consume


def clears_match_threshold(score: float) -> bool:
    return score >= MATCH_THRESHOLD


# ---------------------------------------------------------------- Stockpile (C.2/C.3)
def allocate_stockpile(ordered_fitting_soll: list[tuple[str, dict[int, tuple[float, str]]]],
                        available_by_type: dict[int, float]) -> dict[str, dict[int, float]]:
    """C.2: greedy priority allocation - `ordered_fitting_soll` is
    [(fitting_id, {type_id: (required_total, item_class)}), ...] in
    doctrine list order (= priority, highest first). Returns
    {fitting_id: {type_id: allocated_qty}}."""
    remaining = dict(available_by_type)
    allocation: dict[str, dict[int, float]] = {}
    for fitting_id, soll in ordered_fitting_soll:
        allocation[fitting_id] = {}
        for type_id, (required, _cls) in soll.items():
            have = remaining.get(type_id, 0.0)
            take = min(required, have)
            allocation[fitting_id][type_id] = take
            remaining[type_id] = have - take
    return allocation


def stockpile_deviation(type_id: int, required_total: float, allocated: float, item_class: str,
                         cargo_tolerance_pct: float) -> tuple[float, Optional[str]]:
    """C.3: returns (shortfall, severity). severity is None when there's no
    deviation at all (allocated fully covers the tolerance-adjusted Soll)."""
    shortfall = max(0.0, required_total - allocated)
    if shortfall <= 0:
        return 0.0, None
    if item_class == "exact":
        return shortfall, SEVERITY_CRITICAL
    threshold = math.ceil(cargo_tolerance_pct * required_total)
    if allocated <= 0:
        return shortfall, SEVERITY_CRITICAL
    if allocated < threshold:
        return shortfall, SEVERITY_CRITICAL
    return shortfall, SEVERITY_TOLERABLE


# ---------------------------------------------------------------- Aggregation / ampel (B.8)
def contract_ampel(last_synced_at: Optional[str], contract_target: int,
                    valid_contracts: int, tolerable_contracts: int) -> str:
    if last_synced_at is None:
        return AMPEL_GRAY
    if contract_target == 0:
        return AMPEL_GRAY
    if valid_contracts >= contract_target:
        return AMPEL_GREEN
    if valid_contracts + tolerable_contracts >= contract_target or (valid_contracts + tolerable_contracts) > 0:
        return AMPEL_YELLOW
    return AMPEL_RED


def stockpile_ampel(assets_available: bool, stockpile_target: int, worst_severity: Optional[str]) -> str:
    if not assets_available:
        return AMPEL_GRAY
    if stockpile_target == 0:
        return AMPEL_GRAY
    if worst_severity == SEVERITY_CRITICAL:
        return AMPEL_RED
    if worst_severity == SEVERITY_TOLERABLE:
        return AMPEL_YELLOW
    return AMPEL_GREEN


def worst_ampel(ampels: list[str]) -> str:
    """Worst-of aggregation (B.8) - gray never participates; if every input
    is gray, the result is gray."""
    non_gray = [a for a in ampels if a != AMPEL_GRAY]
    if not non_gray:
        return AMPEL_GRAY
    return max(non_gray, key=lambda a: AMPEL_SEVERITY_ORDER.index(a))


def worst_severity(severities: list[Optional[str]]) -> Optional[str]:
    """Worst-of aggregation for stockpile item severities (critical > tolerable
    > info > None) - mirrors worst_ampel's own shape, for engine.
    aggregate_stockpile_rows combining several fittings' rows for the same
    item into one. None never participates (no deviation at all); if every
    input is None, the result is None (no shortfall anywhere)."""
    real = [s for s in severities if s is not None]
    if not real:
        return None
    return max(real, key=lambda s: _SEVERITY_RANK[s])

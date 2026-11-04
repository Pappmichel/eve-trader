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
    CONSUME_SECTIONS, DEVIATION_KIND_EQUIVALENT_VARIANT, DEVIATION_KIND_EXTRA, DEVIATION_KIND_MISSING,
    DEVIATION_KIND_SHORT,
    DEVIATION_KIND_WRONG_VARIANT, EXACT_SECTIONS, MATCH_THRESHOLD, MATCH_WEIGHT_CONSUME, MATCH_WEIGHT_EXACT,
    SEVERITY_CRITICAL, SEVERITY_INFO, SEVERITY_TOLERABLE, VALIDATION_INVALID, VALIDATION_TOLERABLE,
    VALIDATION_UNMATCHED, VALIDATION_VALID,
)
from .models import ContractItemRow, DeviationRow, FittingItem

_SEVERITY_RANK = {SEVERITY_CRITICAL: 2, SEVERITY_TOLERABLE: 1, SEVERITY_INFO: 0}

#: type_id -> every type of its equivalence class, itself included
#: (doctrine/equivalents.lookup); None means exact matching only.
EquivalentsOf = Callable[[int], tuple[int, ...]]


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
                          stockpile_target: int, contract_target: int = 0,
                          valid_contracts: int = 0) -> dict[int, tuple[float, str]]:
    """Returns {type_id: (required_total, item_class)} for stockpile
    validation - item_class is "exact" or "consume" (drives C.3's severity
    rule). Unlike build_contract_soll, the hull IS a normal position here
    (Phase 2 A.3/C.1: v1 counts type quantities, no fitted-ship detection).

    GitHub issue #36: everything is multiplied by stockpile_target *plus*
    however many more outstanding contracts are still needed to reach
    contract_target (max(0, contract_target - valid_contracts)) - confirmed
    with the user (additive, not a max()): the spare-stock buffer
    (stockpile_target) and the materials still needed to actually create
    more contracts are both real, simultaneous demand, not alternatives to
    each other. Before this, an empty/low stockpile_target with a large
    contract shortfall showed as "nothing needed" even though real material
    was required to close that gap."""
    total_sets = stockpile_target + max(0, contract_target - valid_contracts)
    exact, consume = build_contract_soll(items)
    exact[hull_type_id] = exact.get(hull_type_id, 0.0) + 1.0
    result: dict[int, tuple[float, str]] = {}
    for type_id, qty in exact.items():
        result[type_id] = (qty * total_sets, "exact")
    for type_id, qty in consume.items():
        result[type_id] = (qty * total_sets, "consume")
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


# ---------------------------------------------------------------- Equivalent variants
def substitute_equivalents(exact_soll: dict[int, float], consume_soll: dict[int, float],
                           ist: dict[int, float], equivalents_of: Optional[EquivalentsOf],
                           ) -> tuple[dict[int, float], list[tuple[int, int, float]]]:
    """Moves delivered units of a functionally identical variant onto a Soll
    type the contract is short of (True Sansha hardener expected, Dark Blood
    delivered). Only a variant's *surplus* moves - units its own Soll line
    needs stay put - and every unit moves at most once, so one delivered
    module never covers two positions. Deterministic: Soll types and donors
    in type_id order.

    Returns (adjusted Ist, [(wanted_type, used_type, qty), ...])."""
    if equivalents_of is None:
        return dict(ist), []
    adjusted = dict(ist)
    need = {t: exact_soll.get(t, 0.0) + consume_soll.get(t, 0.0) for t in set(exact_soll) | set(consume_soll)}
    surplus = {t: max(0.0, q - need.get(t, 0.0)) for t, q in ist.items()}
    substitutions: list[tuple[int, int, float]] = []
    for wanted in sorted(need):
        deficit = need[wanted] - adjusted.get(wanted, 0.0)
        if deficit <= 0:
            continue
        for donor in equivalents_of(wanted):
            if donor == wanted or surplus.get(donor, 0.0) <= 0:
                continue
            moved = min(deficit, surplus[donor])
            surplus[donor] -= moved
            adjusted[donor] -= moved
            adjusted[wanted] = adjusted.get(wanted, 0.0) + moved
            substitutions.append((wanted, donor, moved))
            deficit -= moved
            if deficit <= 0:
                break
    return {t: q for t, q in adjusted.items() if q > 0}, substitutions


# ---------------------------------------------------------------- Deviations (B.4)
def compute_deviations(contract_id: int, exact_soll: dict[int, float], consume_soll: dict[int, float],
                        contract_items: list[ContractItemRow], hull_type_id: int,
                        cargo_tolerance_pct: float, strict_extras: bool = False,
                        equivalents_of: Optional[EquivalentsOf] = None) -> list[DeviationRow]:
    """B.4's full deviation table, plus B.3's is_included=False "requested
    consideration" extras. Returns at most one DeviationRow per (type_id,
    kind) pair - see this module's own docstring on why exact/consume are
    merged defensively before emitting (a type_id could in principle appear
    in both classes across different fitting lines).

    With `equivalents_of`, delivered equivalent variants first cover the
    Soll lines they can stand in for (substitute_equivalents). Each pair is
    reported as two info rows of kind equivalent_variant - the fitted type
    (expected qty, units the twin covered as actual) and the delivered twin
    (0 expected, units used) - so the contract status is unaffected."""
    ist, substitutions = substitute_equivalents(
        exact_soll, consume_soll, build_contract_ist(contract_items, hull_type_id), equivalents_of)
    rows: dict[tuple[int, str], DeviationRow] = {}
    for wanted, used, qty in substitutions:
        wanted_expected = exact_soll.get(wanted, 0.0) + consume_soll.get(wanted, 0.0)
        for type_id, expected in ((wanted, wanted_expected), (used, 0.0)):
            key = (type_id, DEVIATION_KIND_EQUIVALENT_VARIANT)
            prev = rows.get(key)
            actual = qty + (prev.actual_qty if prev is not None else 0.0)
            rows[key] = DeviationRow(contract_id=contract_id, type_id=type_id, kind=DEVIATION_KIND_EQUIVALENT_VARIANT,
                                     severity=SEVERITY_INFO, expected_qty=expected, actual_qty=actual)

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
    return allocate_stockpile_with_equivalents(ordered_fitting_soll, available_by_type, None)[0]


def allocate_stockpile_with_equivalents(
        ordered_fitting_soll: list[tuple[str, dict[int, tuple[float, str]]]],
        available_by_type: dict[int, float], equivalents_of: Optional[EquivalentsOf],
) -> tuple[dict[str, dict[int, float]], dict[str, dict[int, dict[int, float]]]]:
    """allocate_stockpile plus equivalent variants. Pass 1 is the exact
    allocation (every fitting takes its own types, in priority order); pass 2
    fills what is still short from the leftover stock of equivalent variants,
    again in priority order. Two passes, so a twin's stock goes to another
    fitting only after every fitting that names that twin exactly had its turn.

    Returns (allocation incl. variants {fitting_id: {type_id: qty}},
    variants used {fitting_id: {type_id: {variant_type_id: qty}}})."""
    remaining = dict(available_by_type)
    allocation: dict[str, dict[int, float]] = {}
    for fitting_id, soll in ordered_fitting_soll:
        allocation[fitting_id] = {}
        for type_id, (required, _cls) in soll.items():
            have = remaining.get(type_id, 0.0)
            take = min(required, have)
            allocation[fitting_id][type_id] = take
            remaining[type_id] = have - take
    variants: dict[str, dict[int, dict[int, float]]] = {fid: {} for fid, _ in ordered_fitting_soll}
    if equivalents_of is None:
        return allocation, variants
    for fitting_id, soll in ordered_fitting_soll:
        for type_id, (required, _cls) in soll.items():
            short = required - allocation[fitting_id][type_id]
            if short <= 0:
                continue
            for other in equivalents_of(type_id):
                if other == type_id or remaining.get(other, 0.0) <= 0:
                    continue
                take = min(short, remaining[other])
                remaining[other] -= take
                allocation[fitting_id][type_id] += take
                used = variants[fitting_id].setdefault(type_id, {})
                used[other] = used.get(other, 0.0) + take
                short -= take
                if short <= 0:
                    break
    return allocation, variants


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


def max_assemblable(per_set: dict[int, tuple[float, str]],
                    available_by_type: dict[int, float],
                    equivalents_of: Optional[EquivalentsOf] = None) -> tuple[int, dict[int, dict]]:
    """How many complete sets of one fitting the stock covers right now.

    `per_set` is build_stockpile_soll(..., stockpile_target=1): one hull, the
    exact modules and every consumable at its full quantity (no cargo
    tolerance - confirmed with the user 2026-11-04). The fitting gets top
    priority: the whole stock counts for it, whatever other fittings'
    stockpile allocation would claim. Returns (max_sets, {type_id: detail})
    where detail has per_set, item_class, on_hand, sets (how many sets this
    item alone allows) and short_for_next (missing for max_sets + 1).
    An empty requirement allows 0 sets.

    With `equivalents_of`, the stock of every equivalent variant counts:
    the lines of one equivalence class share one pool (their own stock plus
    every other variant's), so `on_hand` includes `variant_on_hand` and one
    unit never serves two lines."""
    if equivalents_of is not None:
        return _max_assemblable_pooled(per_set, available_by_type, equivalents_of)
    details: dict[int, dict] = {}
    for type_id, (qty, item_class) in per_set.items():
        if qty <= 0:
            continue
        on_hand = max(0.0, float(available_by_type.get(type_id, 0.0)))
        details[type_id] = {"per_set": qty, "item_class": item_class, "on_hand": on_hand,
                            "sets": int(math.floor(on_hand / qty + 1e-9))}
    max_sets = min((d["sets"] for d in details.values()), default=0)
    for d in details.values():
        d["short_for_next"] = max(0.0, (max_sets + 1) * d["per_set"] - d["on_hand"])
    return max_sets, details


def _max_assemblable_pooled(per_set: dict[int, tuple[float, str]], available_by_type: dict[int, float],
                            equivalents_of: EquivalentsOf) -> tuple[int, dict[int, dict]]:
    stock = {t: max(0.0, float(q)) for t, q in available_by_type.items()}
    lines = {t: v for t, v in per_set.items() if v[0] > 0}
    groups: dict[tuple[int, ...], list[int]] = {}
    for type_id in sorted(lines):
        groups.setdefault(tuple(sorted(equivalents_of(type_id))), []).append(type_id)

    def _allocate(group_types: tuple[int, ...], line_types: list[int], sets: int) -> dict[int, float]:
        """Pool split over the lines for `sets` sets: own stock first, then
        the shared rest. Whatever is left over stays with the line whose own
        stock it is (as far as it goes), the rest goes to the last line."""
        pool = sum(stock.get(t, 0.0) for t in group_types)
        target = {t: lines[t][0] * sets for t in line_types}
        alloc = {t: min(stock.get(t, 0.0), target[t]) for t in line_types}
        left = pool - sum(alloc.values())
        for t in line_types:
            take = min(max(0.0, target[t] - alloc[t]), left)
            alloc[t] += take
            left -= take
        for t in line_types:
            back = min(max(0.0, stock.get(t, 0.0) - alloc[t]), left)
            alloc[t] += back
            left -= back
        alloc[line_types[-1]] += max(0.0, left)
        return alloc

    group_sets: dict[tuple[int, ...], int] = {}
    for group_types, line_types in groups.items():
        pool = sum(stock.get(t, 0.0) for t in group_types)
        group_sets[group_types] = int(math.floor(pool / sum(lines[t][0] for t in line_types) + 1e-9))
    max_sets = min(group_sets.values(), default=0)

    details: dict[int, dict] = {}
    for group_types, line_types in groups.items():
        sets = group_sets[group_types]
        now = _allocate(group_types, line_types, sets)
        nxt = _allocate(group_types, line_types, max_sets + 1)
        for t in line_types:
            qty, item_class = lines[t]
            own = stock.get(t, 0.0)
            details[t] = {"per_set": qty, "item_class": item_class, "on_hand": now[t],
                          "variant_on_hand": max(0.0, now[t] - own), "sets": sets,
                          "short_for_next": max(0.0, (max_sets + 1) * qty - nxt[t])}
    return max_sets, details


# ---------------------------------------------------------------- Aggregation / ampel (B.8)
def contract_ampel(last_synced_at: Optional[str], contract_target: int,
                    valid_contracts: int, tolerable_contracts: int) -> str:
    if last_synced_at is None:
        return AMPEL_GRAY
    if contract_target == 0:
        return AMPEL_GRAY
    if valid_contracts >= contract_target:
        return AMPEL_GREEN
    # GitHub issue #66 (found in a full-codebase audit 2026-08-21): the
    # `>= contract_target` disjunct was redundant - contract_target is
    # already >= 1 here (the ==0 case returned above) and the green branch
    # above already excluded valid_contracts >= contract_target on its own,
    # so ">= contract_target" always implies "> 0" wherever this line is
    # even reached. Simplified to what it was actually equivalent to.
    if (valid_contracts + tolerable_contracts) > 0:
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

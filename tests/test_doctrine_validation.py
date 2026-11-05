from eve_trader.doctrine import validation as v
from eve_trader.doctrine.constants import (
    DEVIATION_KIND_EXTRA, DEVIATION_KIND_MISSING, DEVIATION_KIND_SHORT, DEVIATION_KIND_WRONG_VARIANT,
    SEVERITY_CRITICAL, SEVERITY_INFO, SEVERITY_TOLERABLE, VALIDATION_INVALID, VALIDATION_TOLERABLE,
    VALIDATION_UNMATCHED, VALIDATION_VALID,
)
from eve_trader.doctrine.models import ContractItemRow, FittingItem

HULL = 999


def _fitting_items():
    return [
        FittingItem("f1", 1, "low", 100, 1),
        FittingItem("f1", 2, "high", 200, 1),
        FittingItem("f1", 3, "cargo", 300, 10),
        FittingItem("f1", 4, "charge", 400, 1),
    ]


# --------------------------------------------------------------- Soll construction
def test_build_contract_soll_excludes_hull_and_splits_classes():
    exact, consume = v.build_contract_soll(_fitting_items())
    assert exact == {100: 1.0, 200: 1.0}
    assert consume == {300: 10.0, 400: 1.0}


def test_build_contract_soll_fuelbay_and_ship_maintenance_bay_are_consume_tolerant(monkeypatch):
    # GitHub issue #18: Fuel Bay / Ship Maintenance Bay contents are
    # quantity-flexible like cargo/drones, not an exact-match position -
    # a seller doesn't necessarily fill a capital's fuel bay to the exact
    # reference level.
    items = [
        FittingItem("f1", 1, "fuelbay", 500, 2500),
        FittingItem("f1", 2, "shipmaintenancebay", 600, 1),
    ]
    exact, consume = v.build_contract_soll(items)
    assert exact == {}
    assert consume == {500: 2500.0, 600: 1.0}


def test_charge_merges_with_existing_cargo_quantity_not_additive():
    items = [FittingItem("f1", 1, "cargo", 300, 5), FittingItem("f1", 2, "charge", 300, 1)]
    _exact, consume = v.build_contract_soll(items)
    assert consume == {300: 5.0}  # not 6 - charge's floor-of-1 is absorbed into the existing cargo qty


def test_charge_only_type_gets_floor_of_one():
    items = [FittingItem("f1", 1, "charge", 400, 1)]
    _exact, consume = v.build_contract_soll(items)
    assert consume == {400: 1.0}


def test_build_stockpile_soll_includes_hull_and_multiplies_by_target():
    soll = v.build_stockpile_soll(_fitting_items(), hull_type_id=HULL, stockpile_target=3)
    assert soll[HULL] == (3.0, "exact")
    assert soll[100] == (3.0, "exact")
    assert soll[200] == (3.0, "exact")
    assert soll[300] == (30.0, "consume")
    assert soll[400] == (3.0, "consume")


def test_build_stockpile_soll_adds_contract_shortfall_to_stockpile_target():
    # GitHub issue #36: materials needed to create more outstanding
    # contracts (contract_target - valid_contracts) are real, simultaneous
    # demand on top of the separate spare-stock buffer (stockpile_target),
    # confirmed additive (not max()) with the user.
    soll = v.build_stockpile_soll(_fitting_items(), hull_type_id=HULL, stockpile_target=1,
                                   contract_target=5, valid_contracts=2)
    # total_sets = 1 (stockpile_target) + max(0, 5 - 2) = 4
    assert soll[HULL] == (4.0, "exact")
    assert soll[300] == (40.0, "consume")


def test_build_stockpile_soll_contract_shortfall_floors_at_zero_when_already_met():
    # valid_contracts already meets/exceeds contract_target - no extra
    # demand on top of stockpile_target, not a negative subtraction.
    soll = v.build_stockpile_soll(_fitting_items(), hull_type_id=HULL, stockpile_target=2,
                                   contract_target=3, valid_contracts=5)
    assert soll[HULL] == (2.0, "exact")


def test_build_stockpile_soll_defaults_to_stockpile_target_only():
    # contract_target/valid_contracts default to 0 - callers that don't pass
    # them (or a fitting with no contract_target at all) keep the exact
    # pre-issue-#36 behavior.
    soll = v.build_stockpile_soll(_fitting_items(), hull_type_id=HULL, stockpile_target=3)
    assert soll[HULL] == (3.0, "exact")


# --------------------------------------------------------------- hull gate / Ist
def test_hull_gate_requires_included_only_not_singleton():
    # Confirmed live (2026-08-19) against a real contract: a genuinely
    # assembled/fitted ship's own hull line can still come back with
    # is_singleton=False from ESI's contract-items endpoint - unlike the
    # asset endpoints, that field isn't a reliable packaged-vs-assembled
    # signal here, so the gate no longer requires it.
    assert v.hull_gate_satisfied([ContractItemRow(1, 0, HULL, 1, True, True)], HULL)
    assert v.hull_gate_satisfied([ContractItemRow(1, 0, HULL, 1, True, False)], HULL)  # is_singleton=False, still gates
    assert not v.hull_gate_satisfied([ContractItemRow(1, 0, HULL, 1, False, True)], HULL)  # not included
    assert not v.hull_gate_satisfied([ContractItemRow(1, 0, 111, 1, True, True)], HULL)  # wrong type


def test_build_contract_ist_excludes_hull_and_non_included_items():
    items = [
        ContractItemRow(1, 0, HULL, 1, True, True),
        ContractItemRow(1, 1, 100, 1, True, False),
        ContractItemRow(1, 2, 999999, 5, False, False),  # a requested "want", not included
    ]
    ist = v.build_contract_ist(items, HULL)
    assert ist == {100: 1.0}


def test_build_contract_ist_sums_separately_stacked_charges_regardless_of_damage():
    # GitHub issue #18 - "ammunition of fitted ships might be damaged, that
    # should be ignored": a real capital contract's item list has several
    # separate 1-unit stacks of the same charge type_id (each at a different
    # damage %, shown as its own line in the contract window) plus one
    # undamaged bulk stack - ESI's contract-items response has no damage
    # field at all (see build_contract_ist's own docstring), so every stack
    # of the same type_id must sum together the same way regardless.
    items = [
        ContractItemRow(1, 0, HULL, 1, True, True),
        ContractItemRow(1, 1, 400, 1, True, False),   # "55% damaged" in-game, invisible to ESI
        ContractItemRow(1, 2, 400, 1, True, False),   # "50% damaged"
        ContractItemRow(1, 3, 400, 16, True, False),  # undamaged bulk stack
    ]
    ist = v.build_contract_ist(items, HULL)
    assert ist == {400: 18.0}


# --------------------------------------------------------------- deviations (B.4)
def test_exact_class_missing_item_is_critical():
    exact, consume = {100: 1.0}, {}
    devs = v.compute_deviations(1, exact, consume, [], HULL, cargo_tolerance_pct=0.9)
    assert len(devs) == 1
    assert devs[0].kind == DEVIATION_KIND_MISSING
    assert devs[0].severity == SEVERITY_CRITICAL


def test_exact_class_short_is_always_critical_no_tolerance():
    exact, consume = {100: 2.0}, {}
    items = [ContractItemRow(1, 0, 100, 1, True, False)]
    devs = v.compute_deviations(1, exact, consume, items, HULL, cargo_tolerance_pct=0.5)
    assert devs[0].kind == DEVIATION_KIND_SHORT
    assert devs[0].severity == SEVERITY_CRITICAL


def test_consume_class_within_tolerance_is_tolerable():
    exact, consume = {}, {300: 10.0}
    items = [ContractItemRow(1, 0, 300, 9, True, False)]  # 9/10 = 90%, tolerance 0.9 -> ceil(9)=9, ok
    devs = v.compute_deviations(1, exact, consume, items, HULL, cargo_tolerance_pct=0.9)
    assert len(devs) == 1
    assert devs[0].kind == DEVIATION_KIND_SHORT
    assert devs[0].severity == SEVERITY_TOLERABLE


def test_consume_class_below_tolerance_is_critical():
    exact, consume = {}, {300: 10.0}
    items = [ContractItemRow(1, 0, 300, 5, True, False)]
    devs = v.compute_deviations(1, exact, consume, items, HULL, cargo_tolerance_pct=0.9)
    assert devs[0].severity == SEVERITY_CRITICAL


def test_consume_class_fully_missing_is_critical_not_tolerable():
    exact, consume = {}, {300: 10.0}
    devs = v.compute_deviations(1, exact, consume, [], HULL, cargo_tolerance_pct=0.9)
    assert devs[0].kind == DEVIATION_KIND_MISSING
    assert devs[0].severity == SEVERITY_CRITICAL


def test_extra_item_is_info_by_default():
    items = [ContractItemRow(1, 0, 500, 1, True, False)]
    devs = v.compute_deviations(1, {}, {}, items, HULL, cargo_tolerance_pct=0.9)
    assert devs[0].kind == DEVIATION_KIND_EXTRA
    assert devs[0].severity == SEVERITY_INFO


def test_extra_item_is_tolerable_under_strict_extras():
    items = [ContractItemRow(1, 0, 500, 1, True, False)]
    devs = v.compute_deviations(1, {}, {}, items, HULL, cargo_tolerance_pct=0.9, strict_extras=True)
    assert devs[0].severity == SEVERITY_TOLERABLE


def test_not_included_item_is_extra_info_requested_consideration():
    items = [ContractItemRow(1, 0, 700, 3, False, False)]
    devs = v.compute_deviations(1, {}, {}, items, HULL, cargo_tolerance_pct=0.9)
    assert devs[0].kind == DEVIATION_KIND_EXTRA
    assert devs[0].expected_qty == 0
    assert devs[0].actual_qty == 3


def test_exact_match_produces_no_deviation():
    exact, consume = {100: 1.0}, {}
    items = [ContractItemRow(1, 0, 100, 1, True, False)]
    devs = v.compute_deviations(1, exact, consume, items, HULL, cargo_tolerance_pct=0.9)
    assert devs == []


# --------------------------------------------------------------- wrong_variant (B.6)
def test_wrong_variant_pairs_same_group_same_slot():
    from eve_trader.doctrine.models import DeviationRow
    devs = [
        DeviationRow(1, 100, DEVIATION_KIND_MISSING, SEVERITY_CRITICAL, expected_qty=1, actual_qty=0),
        DeviationRow(1, 200, DEVIATION_KIND_EXTRA, SEVERITY_INFO, expected_qty=0, actual_qty=1),
    ]
    group_id_of = {100: 50, 200: 50}.get
    slot_of = {100: "low", 200: "low"}.get
    result = v.pair_wrong_variants(devs, group_id_of, slot_of)
    kinds = {d.type_id: (d.kind, d.severity) for d in result}
    assert kinds[100] == (DEVIATION_KIND_WRONG_VARIANT, SEVERITY_CRITICAL)
    assert kinds[200] == (DEVIATION_KIND_WRONG_VARIANT, SEVERITY_INFO)


def test_wrong_variant_does_not_pair_different_groups():
    from eve_trader.doctrine.models import DeviationRow
    devs = [
        DeviationRow(1, 100, DEVIATION_KIND_MISSING, SEVERITY_CRITICAL, expected_qty=1, actual_qty=0),
        DeviationRow(1, 200, DEVIATION_KIND_EXTRA, SEVERITY_INFO, expected_qty=0, actual_qty=1),
    ]
    group_id_of = {100: 50, 200: 99}.get
    slot_of = {100: "low", 200: "low"}.get
    result = v.pair_wrong_variants(devs, group_id_of, slot_of)
    kinds = {d.type_id: d.kind for d in result}
    assert kinds[100] == DEVIATION_KIND_MISSING
    assert kinds[200] == DEVIATION_KIND_EXTRA


# --------------------------------------------------------------- status (B.7)
def test_contract_status_unmatched_when_not_matched():
    assert v.contract_status([], matched=False) == VALIDATION_UNMATCHED


def test_contract_status_valid_with_no_deviations():
    assert v.contract_status([], matched=True) == VALIDATION_VALID


def test_contract_status_invalid_with_any_critical():
    from eve_trader.doctrine.models import DeviationRow
    devs = [DeviationRow(1, 100, DEVIATION_KIND_MISSING, SEVERITY_CRITICAL)]
    assert v.contract_status(devs, matched=True) == VALIDATION_INVALID


def test_contract_status_tolerable_with_only_tolerable():
    from eve_trader.doctrine.models import DeviationRow
    devs = [DeviationRow(1, 100, DEVIATION_KIND_SHORT, SEVERITY_TOLERABLE)]
    assert v.contract_status(devs, matched=True) == VALIDATION_TOLERABLE


def test_contract_status_valid_with_only_info():
    from eve_trader.doctrine.models import DeviationRow
    devs = [DeviationRow(1, 100, DEVIATION_KIND_EXTRA, SEVERITY_INFO)]
    assert v.contract_status(devs, matched=True) == VALIDATION_VALID


# --------------------------------------------------------------- matching score (B.5)
def test_match_score_perfect_match_is_one():
    exact, consume = {100: 1.0}, {300: 5.0}
    ist = {100: 1.0, 300: 5.0}
    assert v.match_score(exact, consume, ist) == 1.0


def test_match_score_empty_soll_classes_score_perfectly():
    assert v.match_score({}, {}, {}) == 1.0


def test_match_score_partial_overlap_below_threshold():
    exact, consume = {100: 1.0, 101: 1.0, 102: 1.0, 103: 1.0}, {}
    ist = {100: 1.0}  # only 1/4 exact items present
    score = v.match_score(exact, consume, ist)
    assert not v.clears_match_threshold(score)


# --------------------------------------------------------------- stockpile (C.2/C.3)
def test_allocate_stockpile_greedy_priority_order():
    soll = [
        ("f1", {100: (8.0, "exact")}),
        ("f2", {100: (8.0, "exact")}),
    ]
    alloc = v.allocate_stockpile(soll, {100: 10.0})
    assert alloc["f1"][100] == 8.0
    assert alloc["f2"][100] == 2.0  # only 2 left after f1 takes its full 8


def test_allocate_stockpile_never_overallocates():
    soll = [("f1", {100: (5.0, "exact")})]
    alloc = v.allocate_stockpile(soll, {100: 20.0})
    assert alloc["f1"][100] == 5.0


def test_stockpile_deviation_exact_class_any_shortfall_is_critical():
    shortfall, severity = v.stockpile_deviation(100, required_total=5.0, allocated=4.0, item_class="exact",
                                                  cargo_tolerance_pct=0.9)
    assert shortfall == 1.0
    assert severity == SEVERITY_CRITICAL


def test_stockpile_deviation_consume_class_within_tolerance():
    shortfall, severity = v.stockpile_deviation(300, required_total=10.0, allocated=9.0, item_class="consume",
                                                  cargo_tolerance_pct=0.9)
    assert shortfall == 1.0
    assert severity == SEVERITY_TOLERABLE


def test_stockpile_deviation_no_shortfall_is_none():
    shortfall, severity = v.stockpile_deviation(100, required_total=5.0, allocated=5.0, item_class="exact",
                                                  cargo_tolerance_pct=0.9)
    assert shortfall == 0.0
    assert severity is None


# --------------------------------------------------------------- ampel aggregation (B.8)
def test_contract_ampel_gray_when_never_synced():
    assert v.contract_ampel(None, contract_target=2, valid_contracts=0, tolerable_contracts=0) == "gray"


def test_contract_ampel_gray_when_no_target_set():
    assert v.contract_ampel("2026-01-01", contract_target=0, valid_contracts=0, tolerable_contracts=0) == "gray"


def test_contract_ampel_green_when_target_met():
    assert v.contract_ampel("2026-01-01", contract_target=2, valid_contracts=2, tolerable_contracts=0) == "green"


def test_contract_ampel_yellow_when_partially_covered():
    assert v.contract_ampel("2026-01-01", contract_target=2, valid_contracts=1, tolerable_contracts=0) == "yellow"


def test_contract_ampel_red_when_nothing_viable():
    assert v.contract_ampel("2026-01-01", contract_target=2, valid_contracts=0, tolerable_contracts=0) == "red"


def test_stockpile_ampel_gray_when_assets_unavailable():
    assert v.stockpile_ampel(assets_available=False, stockpile_target=2, worst_severity=None) == "gray"


def test_stockpile_ampel_green_when_no_deviation():
    assert v.stockpile_ampel(assets_available=True, stockpile_target=2, worst_severity=None) == "green"


def test_stockpile_ampel_red_on_critical():
    assert v.stockpile_ampel(assets_available=True, stockpile_target=2, worst_severity=SEVERITY_CRITICAL) == "red"


def test_worst_ampel_prefers_non_gray_and_worst_of_remaining():
    assert v.worst_ampel(["green", "red", "gray"]) == "red"
    assert v.worst_ampel(["gray", "gray"]) == "gray"
    assert v.worst_ampel(["green", "yellow"]) == "yellow"
    assert v.worst_ampel([]) == "gray"


def test_worst_severity_prefers_non_none_and_worst_of_remaining():
    assert v.worst_severity([SEVERITY_TOLERABLE, SEVERITY_CRITICAL, None]) == SEVERITY_CRITICAL
    assert v.worst_severity([None, None]) is None
    assert v.worst_severity([SEVERITY_INFO, SEVERITY_TOLERABLE]) == SEVERITY_TOLERABLE
    assert v.worst_severity([]) is None


# ---------------------------------------------------------------- max assemblable
def test_max_assemblable_min_over_items_and_short_for_next():
    per_set = v.build_stockpile_soll([FittingItem("f", 1, "low", 100, 2),
                                      FittingItem("f", 2, "cargo", 200, 500)], hull_type_id=1, stockpile_target=1)
    max_sets, details = v.max_assemblable(per_set, {1: 5, 100: 7, 200: 1400})
    assert max_sets == 2  # cargo allows 2 (1400 / 500), modules 3, hulls 5
    assert details[200]["sets"] == 2 and details[100]["sets"] == 3 and details[1]["sets"] == 5
    assert details[200]["short_for_next"] == 100  # 3 x 500 - 1400
    assert details[100]["short_for_next"] == 0
    assert details[1]["item_class"] == "exact"


def test_short_for_sets_lists_what_is_missing_for_a_target():
    per_set = v.build_stockpile_soll([FittingItem("f", 1, "low", 100, 2),
                                      FittingItem("f", 2, "cargo", 200, 500)], hull_type_id=1, stockpile_target=1)
    short = v.short_for_sets(per_set, {1: 5, 100: 7, 200: 1400}, 10)
    assert short == {1: 5, 100: 13, 200: 3600}


def test_short_for_sets_is_zero_below_what_the_stock_allows():
    per_set = v.build_stockpile_soll([FittingItem("f", 1, "low", 100, 2)], hull_type_id=1, stockpile_target=1)
    assert v.short_for_sets(per_set, {1: 5, 100: 10}, 5) == {1: 0, 100: 0}


def test_short_for_sets_matches_short_for_next():
    per_set = v.build_stockpile_soll([FittingItem("f", 1, "low", 100, 2)], hull_type_id=1, stockpile_target=1)
    available = {1: 4, 100: 5}
    max_sets, details = v.max_assemblable(per_set, available)
    short = v.short_for_sets(per_set, available, max_sets + 1)
    assert {t: d["short_for_next"] for t, d in details.items()} == short


def test_short_for_sets_counts_equivalent_variants_once():
    # 100 and 101 are twins; both are fitted. 6 units of 101 serve either line,
    # so 3 sets need 12 and 5 of the pool (11 units) leave exactly 1 short.
    per_set = {100: (2.0, "exact"), 101: (2.0, "exact")}
    lookup = lambda t: (100, 101)  # noqa: E731
    short = v.short_for_sets(per_set, {100: 5, 101: 6}, 3, lookup)
    assert sum(short.values()) == 1


def test_max_assemblable_consumables_need_full_quantity():
    per_set = v.build_stockpile_soll([FittingItem("f", 1, "cargo", 200, 100)], hull_type_id=1, stockpile_target=1)
    # 99 of 100 would pass the cargo tolerance on a contract; here it is not a fit.
    max_sets, details = v.max_assemblable(per_set, {1: 3, 200: 99})
    assert max_sets == 0
    assert details[200]["short_for_next"] == 1


def test_max_assemblable_charge_floor_of_one():
    per_set = v.build_stockpile_soll([FittingItem("f", 1, "charge", 300, 1)], hull_type_id=1, stockpile_target=1)
    max_sets, _details = v.max_assemblable(per_set, {1: 2, 300: 1})
    assert max_sets == 1


def test_max_assemblable_empty_stock_and_empty_requirement():
    per_set = v.build_stockpile_soll([], hull_type_id=1, stockpile_target=1)
    assert v.max_assemblable(per_set, {})[0] == 0
    assert v.max_assemblable({}, {1: 10}) == (0, {})

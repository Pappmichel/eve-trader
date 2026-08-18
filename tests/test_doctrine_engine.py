"""Tests for doctrine/engine.py's pure orchestration functions that don't
need real storage/Postgres - match_and_validate_contract's NO_HULL_MATCH
branch, and (as they're added) aggregate_stockpile_rows/fitting_status's
expired-exclusion. Postgres-backed engine tests live in
test_doctrine_storage.py alongside the storage functions they exercise.
"""
from eve_trader import storage
from eve_trader.doctrine import engine
from eve_trader.doctrine.config import DoctrineConfig
from eve_trader.doctrine.constants import SEVERITY_CRITICAL, SEVERITY_TOLERABLE, VALIDATION_UNMATCHED
from eve_trader.doctrine.engine import _Candidate
from eve_trader.doctrine.models import ContractItemRow, Fitting, FittingItem, StockpileRow

HULL_A = 1000
HULL_B = 2000
MODULE = 100


def _candidate(fitting_id: str, hull_type_id: int, items: list[FittingItem]) -> _Candidate:
    fitting = Fitting(fitting_id=fitting_id, doctrine_id="d1", name=fitting_id, hull_type_id=hull_type_id,
                       raw_eft="", contract_target=1, stockpile_target=0)
    from eve_trader.doctrine.validation import build_contract_soll
    exact_soll, consume_soll = build_contract_soll(items)
    return _Candidate(fitting=fitting, exact_soll=exact_soll, consume_soll=consume_soll, items=items)


def test_match_and_validate_contract_no_hull_match_when_no_candidate_hull_present():
    # Contract has neither HULL_A nor HULL_B at all - a totally unrelated
    # item_exchange contract (minerals, a single module, ...), not even a
    # near-miss doctrine sale.
    candidates = [_candidate("f1", HULL_A, [FittingItem("f1", 1, "low", MODULE, 1)])]
    contract_items = [ContractItemRow(1, 1, MODULE, 1, True, True)]

    matched_fitting_id, score, deviations, status = engine.match_and_validate_contract(
        1, None, contract_items, candidates)

    assert matched_fitting_id is None
    assert score == 0.0
    assert deviations == []
    assert status == engine.NO_HULL_MATCH


def test_match_and_validate_contract_unmatched_when_hull_present_but_below_threshold():
    # Hull IS present (a real near-miss - wrong/missing modules) - distinct
    # from NO_HULL_MATCH, must still be a genuine "unmatched" (kept visible).
    candidates = [_candidate("f1", HULL_A, [
        FittingItem("f1", 1, "low", MODULE, 1), FittingItem("f1", 2, "high", MODULE + 1, 1),
    ])]
    contract_items = [ContractItemRow(1, 1, HULL_A, 1, True, True)]  # bare hull, nothing else

    matched_fitting_id, score, deviations, status = engine.match_and_validate_contract(
        1, None, contract_items, candidates)

    assert matched_fitting_id is None
    assert status == VALIDATION_UNMATCHED
    assert status != engine.NO_HULL_MATCH


def test_match_and_validate_contract_no_hull_match_with_no_candidates_at_all():
    contract_items = [ContractItemRow(1, 1, MODULE, 1, True, True)]

    matched_fitting_id, score, deviations, status = engine.match_and_validate_contract(1, None, contract_items, [])

    assert status == engine.NO_HULL_MATCH


# ---------------------------------------------------------------- fitting_status
_CONTRACT_ROW_FIELDS = ("contract_id", "source_role", "for_corporation", "issuer_id", "start_location_id",
                        "status", "title", "price", "date_expired", "matched_fitting_id", "match_score",
                        "validation_status", "synced_at")


def _contract_row(**overrides) -> tuple:
    base = dict(contract_id=1, source_role="doctrine:1", for_corporation=False, issuer_id=42,
                start_location_id=1, status="outstanding", title="t", price=1.0, date_expired=None,
                matched_fitting_id="f1", match_score=1.0, validation_status="valid", synced_at="2026-01-01")
    base.update(overrides)
    return tuple(base[f] for f in _CONTRACT_ROW_FIELDS)


def _fitting(contract_target=2) -> Fitting:
    return Fitting(fitting_id="f1", doctrine_id="d1", name="Fit", hull_type_id=HULL_A, raw_eft="",
                   contract_target=contract_target, stockpile_target=0)


def test_fitting_status_excludes_expired_contracts_from_valid_count(monkeypatch):
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **kwargs: [
        _contract_row(contract_id=1, validation_status="valid", status="outstanding"),
        _contract_row(contract_id=2, validation_status="valid", status="expired"),
    ])
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: "2026-01-01T00:00:00Z")

    status = engine.fitting_status(_fitting(contract_target=2), stockpile_rows=[], assets_available=True)

    assert status.valid_contracts == 1  # the expired one doesn't count, even though validation_status is "valid"


def test_fitting_status_excludes_expired_contracts_from_tolerable_count(monkeypatch):
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **kwargs: [
        _contract_row(contract_id=1, validation_status="tolerable", status="outstanding"),
        _contract_row(contract_id=2, validation_status="tolerable", status="expired"),
    ])
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: "2026-01-01T00:00:00Z")

    status = engine.fitting_status(_fitting(contract_target=2), stockpile_rows=[], assets_available=True)

    assert status.tolerable_contracts == 1


# ---------------------------------------------------------------- aggregate_stockpile_rows
def _stockpile_row(**overrides) -> StockpileRow:
    base = dict(fitting_id="f1", fitting_name="Fit 1", doctrine_id="d1", doctrine_name="Doctrine 1",
                type_id=MODULE, type_name="Damage Control II", slot_section="low", required_total=5.0,
                available=2.0, shortfall=3.0, severity=SEVERITY_CRITICAL)
    base.update(overrides)
    return StockpileRow(**base)


def test_aggregate_stockpile_rows_sums_across_fittings_for_same_type():
    rows = [
        _stockpile_row(fitting_id="f1", required_total=5.0, available=2.0, shortfall=3.0),
        _stockpile_row(fitting_id="f2", required_total=3.0, available=3.0, shortfall=0.0, severity=None),
    ]

    aggregated = engine.aggregate_stockpile_rows(rows)

    assert len(aggregated) == 1
    row = aggregated[0]
    assert row.type_id == MODULE
    assert row.required_total == 8.0
    assert row.available == 5.0
    assert row.shortfall == 3.0
    assert row.fitting_count == 2


def test_aggregate_stockpile_rows_keeps_different_types_separate():
    rows = [_stockpile_row(type_id=MODULE), _stockpile_row(type_id=MODULE + 1, type_name="Other")]

    aggregated = engine.aggregate_stockpile_rows(rows)

    assert {r.type_id for r in aggregated} == {MODULE, MODULE + 1}
    assert all(r.fitting_count == 1 for r in aggregated)


def test_aggregate_stockpile_rows_severity_is_worst_of_contributing_rows():
    rows = [
        _stockpile_row(fitting_id="f1", severity=SEVERITY_TOLERABLE),
        _stockpile_row(fitting_id="f2", severity=SEVERITY_CRITICAL),
    ]

    aggregated = engine.aggregate_stockpile_rows(rows)

    assert aggregated[0].severity == SEVERITY_CRITICAL


def test_aggregate_stockpile_rows_sorted_by_shortfall_descending():
    rows = [
        _stockpile_row(type_id=1, fitting_id="f1", shortfall=1.0),
        _stockpile_row(type_id=2, fitting_id="f1", shortfall=9.0),
        _stockpile_row(type_id=3, fitting_id="f1", shortfall=5.0),
    ]

    aggregated = engine.aggregate_stockpile_rows(rows)

    assert [r.type_id for r in aggregated] == [2, 3, 1]

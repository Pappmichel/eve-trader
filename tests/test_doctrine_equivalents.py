"""Equivalent module variants (functionally identical items such as True
Sansha / Dark Blood Thermal Armor Hardener): SDE class building, contract
substitution, stockpile pooling and the shopping list's cheapest-variant
pick. Storage-free; the storage round trip lives in test_doctrine_storage.py.
"""
import pytest

from eve_trader import sde_equivalence, storage
from eve_trader.doctrine import engine
from eve_trader.doctrine import validation as v
from eve_trader.doctrine.config import DoctrineConfig
from eve_trader.doctrine.constants import (
    DEVIATION_KIND_EQUIVALENT_VARIANT, DEVIATION_KIND_WRONG_VARIANT, SEVERITY_CRITICAL, SEVERITY_INFO,
    VALIDATION_INVALID, VALIDATION_VALID,
)
from eve_trader.doctrine.engine import _Candidate
from eve_trader.doctrine.models import AggregatedStockpileRow, ContractItemRow, Fitting, FittingItem
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production import esi_sync as production_esi_sync
from eve_trader.production import sde

HULL = 1000
SANSHA = 14000      # True Sansha Thermal Armor Hardener
BLOOD = 13999       # Dark Blood Thermal Armor Hardener
OTHER = 500         # unrelated module
CLASSES = {SANSHA: (BLOOD, SANSHA), BLOOD: (BLOOD, SANSHA)}


def _equivalents(type_id):
    return CLASSES.get(type_id, (type_id,))


# ------------------------------------------------------------------- SDE classes
def _hardener_attrs(overrides=None):
    attrs = {50: 1.0, 30: 1.0, 271: 0.55, 633: 8.0, 1692: 4.0}
    attrs.update(overrides or {})
    return attrs


def test_equivalence_classes_merge_identical_types_and_ignore_meta_attributes():
    groups = {SANSHA: 328, BLOOD: 328, OTHER: 328}
    attrs = {SANSHA: _hardener_attrs(), BLOOD: _hardener_attrs({633: 9.0, 1692: 5.0}),
             OTHER: _hardener_attrs({271: 0.5})}
    effects = {SANSHA: [11, 16], BLOOD: [16, 11], OTHER: [11, 16]}
    assert sde_equivalence.equivalence_classes(groups, attrs, effects) == {SANSHA: BLOOD, BLOOD: BLOOD}


def test_equivalence_classes_need_same_group_effects_and_float_noise_is_ignored():
    attrs = {1: {50: 0.1 + 0.2}, 2: {50: 0.3}, 3: {50: 0.3}, 4: {50: 0.3}}
    groups = {1: 10, 2: 10, 3: 11, 4: 10}
    effects = {1: [11], 2: [11], 3: [11], 4: [12]}
    assert sde_equivalence.equivalence_classes(groups, attrs, effects) == {1: 1, 2: 1}


def test_candidate_type_ids_filters_category_market_publish_abyssal_and_fireworks():
    groups = [
        {"groupID": "328", "categoryID": "7", "groupName": "Armor Hardener"},
        {"groupID": "500", "categoryID": "8", "groupName": "Festival Charges"},
        {"groupID": "25", "categoryID": "6", "groupName": "Frigate"},
    ]
    types = [
        {"typeID": "1", "groupID": "328", "published": "1", "marketGroupID": "5"},
        {"typeID": "2", "groupID": "328", "published": "0", "marketGroupID": "5"},
        {"typeID": "3", "groupID": "328", "published": "1", "marketGroupID": ""},
        {"typeID": "4", "groupID": "328", "published": "1", "marketGroupID": "5"},  # abyssal
        {"typeID": "5", "groupID": "500", "published": "1", "marketGroupID": "5"},
        {"typeID": "6", "groupID": "25", "published": "1", "marketGroupID": "5"},
    ]
    assert sde_equivalence.candidate_type_ids(types, groups, {4: 15}) == frozenset({1})


def test_split_attribute_rows_keeps_every_attribute_of_equivalence_types():
    rows = [
        {"typeID": "7", "attributeID": "50", "valueInt": "", "valueFloat": "25"},
        {"typeID": "7", "attributeID": "275", "valueInt": "", "valueFloat": "1"},
        {"typeID": "8", "attributeID": "50", "valueInt": "", "valueFloat": "30"},
    ]
    skill, pi, equiv = sde.split_attribute_rows(rows, frozenset(), frozenset(), frozenset({7}))
    assert equiv == {7: {50: 25.0, 275: 1.0}}
    assert skill == {7: {275: 1.0}}
    assert pi == {}


# ------------------------------------------------------------------- contracts
def _items(*type_ids):
    return [FittingItem("f1", i, "low", t, 1) for i, t in enumerate(type_ids, start=1)]


def _contract(*type_ids):
    rows = [ContractItemRow(1, 0, HULL, 1, True, True)]
    rows += [ContractItemRow(1, i, t, 1, True, False) for i, t in enumerate(type_ids, start=1)]
    return rows


def test_substitute_equivalents_moves_only_surplus_units():
    exact = {SANSHA: 1.0, BLOOD: 1.0}
    ist, subs = v.substitute_equivalents(exact, {}, {BLOOD: 2.0}, _equivalents)
    assert ist == {BLOOD: 1.0, SANSHA: 1.0}
    assert subs == [(SANSHA, BLOOD, 1.0)]
    # One Dark Blood cannot cover both lines.
    ist, subs = v.substitute_equivalents(exact, {}, {BLOOD: 1.0}, _equivalents)
    assert ist == {BLOOD: 1.0}
    assert subs == []


def test_compute_deviations_reports_equivalent_variant_as_info_and_status_stays_valid():
    exact, consume = v.build_contract_soll(_items(SANSHA, OTHER))
    devs = v.compute_deviations(1, exact, consume, _contract(BLOOD, OTHER), HULL, 0.9,
                                equivalents_of=_equivalents)
    by_type = {(d.type_id, d.kind): d for d in devs}
    assert set(by_type) == {(SANSHA, DEVIATION_KIND_EQUIVALENT_VARIANT), (BLOOD, DEVIATION_KIND_EQUIVALENT_VARIANT)}
    assert all(d.severity == SEVERITY_INFO for d in devs)
    assert by_type[(SANSHA, DEVIATION_KIND_EQUIVALENT_VARIANT)].expected_qty == 1.0
    assert by_type[(BLOOD, DEVIATION_KIND_EQUIVALENT_VARIANT)].actual_qty == 1.0
    assert v.contract_status(devs, matched=True) == VALIDATION_VALID


def test_compute_deviations_without_lookup_keeps_exact_matching():
    exact, consume = v.build_contract_soll(_items(SANSHA))
    devs = v.compute_deviations(1, exact, consume, _contract(BLOOD), HULL, 0.9)
    assert v.contract_status(devs, matched=True) == VALIDATION_INVALID


def _candidate(items):
    fitting = Fitting(fitting_id="f1", doctrine_id="d1", name="Fit", hull_type_id=HULL, raw_eft="",
                      contract_target=1, stockpile_target=0)
    exact, consume = v.build_contract_soll(items)
    return _Candidate(fitting=fitting, exact_soll=exact, consume_soll=consume, items=items)


@pytest.fixture
def _sde(monkeypatch):
    monkeypatch.setattr(engine.equivalents, "lookup", lambda: _equivalents)
    monkeypatch.setattr(storage, "get_sde_type", lambda t: (t, 328, f"T{t}", 5.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_slot", lambda t: "low")


def test_match_and_validate_contract_accepts_the_twin(_sde):
    fitting_id, score, devs, status = engine.match_and_validate_contract(
        1, None, _contract(BLOOD, OTHER), [_candidate(_items(SANSHA, OTHER))], DoctrineConfig())
    assert fitting_id == "f1"
    assert score == pytest.approx(1.0)
    assert status == VALIDATION_VALID
    assert {d.kind for d in devs} == {DEVIATION_KIND_EQUIVALENT_VARIANT}


def test_match_and_validate_contract_switch_off_restores_wrong_variant(_sde):
    _fid, _score, devs, status = engine.match_and_validate_contract(
        1, None, _contract(BLOOD, OTHER), [_candidate(_items(SANSHA, OTHER))],
        DoctrineConfig(accept_equivalent_variants=False))
    assert status == VALIDATION_INVALID
    assert DEVIATION_KIND_WRONG_VARIANT in {d.kind for d in devs}


# ------------------------------------------------------------------- stockpile
def test_allocation_takes_exact_stock_first_then_variants_in_priority_order():
    soll = [("f1", {SANSHA: (2.0, "exact")}), ("f2", {BLOOD: (2.0, "exact")})]
    allocation, variants = v.allocate_stockpile_with_equivalents(soll, {SANSHA: 1.0, BLOOD: 2.0}, _equivalents)
    # f2 keeps its own Dark Blood; nothing is left for f1's missing unit.
    assert allocation == {"f1": {SANSHA: 1.0}, "f2": {BLOOD: 2.0}}
    assert variants == {"f1": {}, "f2": {}}
    allocation, variants = v.allocate_stockpile_with_equivalents(soll, {SANSHA: 1.0, BLOOD: 4.0}, _equivalents)
    assert allocation == {"f1": {SANSHA: 2.0}, "f2": {BLOOD: 2.0}}
    assert variants["f1"] == {SANSHA: {BLOOD: 1.0}}


def test_stockpile_rows_count_variant_stock(monkeypatch, _sde):
    cand = _candidate(_items(SANSHA))
    cand.fitting.stockpile_target = 1
    cand.fitting.contract_target = 0
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [cand])
    monkeypatch.setattr(engine, "read_esi", lambda *a, **k: [{"type_id": BLOOD}])
    monkeypatch.setattr(storage, "list_doctrines", lambda: [("d1", "Doctrine")])
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [])
    monkeypatch.setattr(storage, "get_sde_types_bulk",
                        lambda ids: {t: (t, 328, f"T{t}", 5.0, 1, 1, 0, None) for t in ids})
    monkeypatch.setattr(storage, "esi_stock_from_asset_rows",
                        lambda rows, type_ids, loc, **k: {t: (1.0 if t in (BLOOD, HULL) else 0.0) for t in type_ids})

    rows, _ = engine.stockpile_rows_for_doctrine(cfg=DoctrineConfig())

    row = next(r for r in rows if r.type_id == SANSHA)
    assert row.available == 1.0
    assert row.shortfall == 0.0
    assert row.variant_available == 1.0
    assert row.variant_names == [f"T{BLOOD}"]


# ------------------------------------------------------------------- shopping list
class _FakeESI:
    def __init__(self, *a, **k):
        pass

    def structure_order_stats_bulk(self, *a, **k):
        return {}


def test_shopping_list_buys_the_cheapest_variant(monkeypatch, _sde):
    agg = AggregatedStockpileRow(type_id=SANSHA, type_name="True Sansha", required_total=2.0, available=0.0,
                                 shortfall=2.0, severity=SEVERITY_CRITICAL, fitting_count=1)
    monkeypatch.setattr(engine, "stockpile_rows_for_doctrine", lambda doctrine_id, cfg: ([], True))
    monkeypatch.setattr(engine, "aggregate_stockpile_rows", lambda rows: [agg])

    class _Ctx:
        def __init__(self, cfg, extra_type_ids=(), **kwargs):
            assert set(extra_type_ids) == {SANSHA, BLOOD}
            self.home = {}
            self.jita = {SANSHA: CurrentPrice(SANSHA, "", 0.0, 40e6), BLOOD: CurrentPrice(BLOOD, "", 0.0, 25e6)}
            self.cost_indices = self.adjusted_prices = self.selected_decryptors = {}

    monkeypatch.setattr(engine, "_PlanContext", _Ctx)
    monkeypatch.setattr(engine, "unit_cost_detail", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(engine, "_haul_volume", lambda *a: 5.0)
    monkeypatch.setattr(engine.hubs, "hub_pricing", lambda *a, **k: engine.hubs.HubPricing())
    monkeypatch.setattr(production_esi_sync, "list_capability_characters", lambda *a, **k: [])
    monkeypatch.setattr(engine, "ESIClient", _FakeESI)
    monkeypatch.setattr(engine.buyer_sources, "collect", lambda type_ids, cfg: None)

    rows, _ = engine.shopping_list(cfg=DoctrineConfig(import_cost_per_m3=0.0))

    assert len(rows) == 1
    row = rows[0]
    assert row.type_id == SANSHA
    assert row.buy_type_id == BLOOD
    assert row.buy_type_name == f"T{BLOOD}"
    assert row.recommended_source == "Jita"
    assert row.total_cost == pytest.approx(row.jita_landed_price * 2)
    assert row.jita_landed_price < 30e6


# ------------------------------------------------------------------- tenant edits
def test_effective_classes_apply_exclusions_manual_adds_and_disabled_groups():
    from eve_trader.doctrine.equivalents import effective_classes
    sde_map = {1: 1, 2: 1, 3: 1, 10: 10, 11: 10}
    class_of, members = effective_classes(sde_map, [(3, None, True), (20, 11, False)], set())
    assert members == {1: (1, 2), 10: (10, 11, 20)}
    assert 3 not in class_of
    # A disabled group drops out; excluding one of a pair dissolves it.
    class_of, members = effective_classes(sde_map, [(2, None, True)], {11})
    assert members == {1: (1, 3)}
    _class_of, members = effective_classes(sde_map, [(11, None, True)], set())
    assert 10 not in members
    # Two items without an SDE group form a manual group; chains and cycles resolve.
    _class_of, members = effective_classes({}, [(31, 30, False), (32, 31, False)], set())
    assert members == {-30: (30, 31, 32)}
    _class_of, members = effective_classes({}, [(40, 41, False), (41, 40, False)], set())
    assert len(members) == 1 and set(next(iter(members.values()))) == {40, 41}


def test_tenant_edits_follow_the_items_through_an_sde_renumbering():
    """An SDE update recomputes class ids (smallest member); edits are keyed
    by item, so they still apply to the same items afterwards."""
    from eve_trader.doctrine.equivalents import effective_classes
    edits = [(3, None, True), (50, 2, False)]
    before = effective_classes({1: 1, 2: 1, 3: 1}, edits, {2})[1]
    assert before == {}  # group switched off
    # Type 1 left the SDE group, so the class id becomes 2.
    _class_of, after = effective_classes({2: 2, 3: 2, 4: 2}, edits, set())
    assert after == {2: (2, 4, 50)}
    assert effective_classes({2: 2, 3: 2, 4: 2}, edits, {2})[1] == {}

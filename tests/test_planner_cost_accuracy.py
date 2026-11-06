"""Regression tests for the planner cost-accuracy fixes: the per-unit
material quantity in cost estimates, and invention cost in build cost."""

import pytest

from eve_trader import storage
from eve_trader.config import TRADING_CONFIG
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.models import InventionResult

PRODUCT = 100
MATERIAL = 300


@pytest.fixture(autouse=True)
def _zero_broker_fee(monkeypatch):
    monkeypatch.setattr(TRADING_CONFIG, "jita_buy_broker_fee", 0.0)


@pytest.fixture(autouse=True)
def _no_storage_side_reads(monkeypatch):
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 4)
    monkeypatch.setattr(storage, "get_blueprint_skills", lambda blueprint_type_id, activity_id: [])
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id, **kwargs: None)
    monkeypatch.setattr(storage, "manual_bpo_best_me_te", lambda bp_type_id: None)
    monkeypatch.setattr(storage, "get_manual_blueprint_copy_cost_per_run", lambda type_id: None)
    monkeypatch.setattr(storage, "get_sde_type",
                        lambda type_id: (type_id, 958, f"Type {type_id}", 1.0, 1, 1125, 0, None))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(engine, "shared_production_owner_ids", lambda data_kind: (None, None))


def _quote(type_id, sell):
    return CurrentPrice(type_id=type_id, updated="2026-01-01", buy=sell * 0.9, sell=sell)


def _setup_single_material(monkeypatch, base_qty, product_qty=1.0):
    monkeypatch.setattr(
        storage, "get_blueprint_for_product",
        lambda type_id: (200, 1, product_qty) if type_id == PRODUCT else None)
    monkeypatch.setattr(
        storage, "get_blueprint_materials",
        lambda blueprint_id, activity_id: [(MATERIAL, base_qty)] if blueprint_id == 200 else [])


def _detail(cfg=None, home=None, t2_memo=None):
    cfg = cfg or ProductionConfig()
    home = home or {MATERIAL: _quote(MATERIAL, 100.0), PRODUCT: _quote(PRODUCT, 10_000_000.0)}
    return engine.unit_cost_detail(
        PRODUCT, cfg, home, {}, memo={}, selected_decryptors={},
        t2_memo={} if t2_memo is None else t2_memo, cost_indices={}, adjusted_prices={})


def _material_mult():
    mult, _, _ = engine._activity_mods("Tech I", PRODUCT, ProductionConfig(), {}, 200)
    return mult


# --- unit material quantity (no per-run ceil) -------------------------------

def test_unit_material_qty_keeps_fraction_and_floors_at_one():
    assert engine._unit_material_qty(3, 0.9) == pytest.approx(2.7)
    assert engine._unit_material_qty(1, 0.9) == 1.0
    assert engine._unit_material_qty(1, 0.5) == 1.0
    assert engine._unit_material_qty(10, 0.0) == 1.0


def test_unit_cost_detail_prices_fractional_material_quantity(monkeypatch):
    _setup_single_material(monkeypatch, base_qty=3)
    mult = _material_mult()
    assert 3 * mult < 3  # ME reduces the quantity below the old per-run ceil of 3
    _, build_cost, _ = _detail()
    assert build_cost == pytest.approx(3 * mult * 100.0)
    # the old one-run ceil would have priced 3 units
    assert build_cost < 3 * 100.0


def test_unit_cost_detail_base_quantity_one_stays_one_unit(monkeypatch):
    _setup_single_material(monkeypatch, base_qty=1)
    _, build_cost, _ = _detail()
    assert build_cost == pytest.approx(100.0)


def test_unit_quantity_differs_from_hundred_run_batch_by_under_one_unit_per_run():
    # Real batch rounding over 100 runs is ceil(base * mult * 100); amortized
    # per run that is within 0.01 unit of the unrounded per-unit quantity.
    for base_qty, mult in ((3, 0.9), (7, 0.87), (25, 0.9405), (1, 0.9)):
        per_run_batch = engine._material_qty(base_qty, mult, 100) / 100
        unit = engine._unit_material_qty(base_qty, mult)
        assert 0 <= per_run_batch - unit < 0.01 + 1e-9
        # the one-run ceil is the coarse one
        assert engine._material_qty(base_qty, mult, 1) >= per_run_batch - 1e-9


def test_unit_cost_memoized_path_matches_detail_for_fractional_quantity(monkeypatch):
    _setup_single_material(monkeypatch, base_qty=3)
    cfg = ProductionConfig()
    home = {MATERIAL: _quote(MATERIAL, 100.0)}
    cost = engine._unit_cost(PRODUCT, cfg, home, {}, {}, {}, {}, {}, {})
    assert cost == pytest.approx(3 * _material_mult() * 100.0)


# --- invention cost in build cost -------------------------------------------

def _chosen(expected_cost_per_run, output_runs=10.0):
    return InventionResult(
        t1_blueprint_type_id=1000, t1_blueprint_name="T1 BPO", product_type_id=200,
        product_name="T2 Thing", decryptor="None", probability=0.5, output_runs=output_runs,
        datacore_cost=0.0, decryptor_cost=0.0, relic_cost=0.0, total_attempt_cost=0.0,
        expected_cost_per_success=None, expected_cost_per_run=expected_cost_per_run,
        me=2, te=4, material_savings_per_run=0.0, net_cost_per_run=0.0)


def _setup_tech_ii(monkeypatch, chosen, product_qty=2.0, base_qty=10):
    _setup_single_material(monkeypatch, base_qty=base_qty, product_qty=product_qty)
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id",
                        lambda blueprint_id: (object(),))
    monkeypatch.setattr(engine, "_tech_ii_mods", lambda *a, **k: (1.0, 1.0, "None", chosen))


def test_tech_ii_build_cost_includes_invention_cost_per_unit(monkeypatch):
    # materials: 10 units * 100 ISK per run, 2 units per run -> 500 per unit
    _setup_tech_ii(monkeypatch, _chosen(expected_cost_per_run=1_000.0))
    _, build_cost, _ = _detail()
    # invention 1,000 ISK per run / 2 units per run = 500 per unit
    assert build_cost == pytest.approx(500.0 + 500.0)


def test_tech_ii_without_priced_invention_adds_nothing(monkeypatch):
    _setup_tech_ii(monkeypatch, _chosen(expected_cost_per_run=None))
    _, build_cost, _ = _detail()
    assert build_cost == pytest.approx(500.0)


def test_tech_ii_without_chosen_invention_adds_nothing(monkeypatch):
    _setup_tech_ii(monkeypatch, None)
    _, build_cost, _ = _detail()
    assert build_cost == pytest.approx(500.0)


def test_manual_bpc_cost_replaces_invention_instead_of_adding_to_it(monkeypatch):
    _setup_tech_ii(monkeypatch, _chosen(expected_cost_per_run=1_000.0))
    monkeypatch.setattr(storage, "get_manual_blueprint_copy_cost_per_run", lambda type_id: 200.0)
    _, build_cost, _ = _detail()
    # 500 materials + 200 / 2 units for the bought copy, no invention on top
    assert build_cost == pytest.approx(500.0 + 100.0)


def test_non_invention_item_cost_is_unchanged(monkeypatch):
    _setup_single_material(monkeypatch, base_qty=10, product_qty=2.0)
    _, build_cost, _ = _detail()
    assert build_cost == pytest.approx(10 * _material_mult() * 100.0 / 2.0)


def test_unit_cost_and_detail_agree_for_tech_ii(monkeypatch):
    _setup_tech_ii(monkeypatch, _chosen(expected_cost_per_run=1_000.0))
    home = {MATERIAL: _quote(MATERIAL, 100.0)}
    cost = engine._unit_cost(PRODUCT, ProductionConfig(), home, {}, {}, {}, {}, {}, {})
    _, build_cost, _ = _detail(home=home)
    assert cost == pytest.approx(build_cost) == pytest.approx(1_000.0)


def test_invention_cost_flips_build_to_buy(monkeypatch):
    # Materials alone make building (500) cheaper than buying (800); with the
    # 500 per unit invention cost building costs 1,000 and buying wins.
    _setup_tech_ii(monkeypatch, _chosen(expected_cost_per_run=1_000.0))
    home = {MATERIAL: _quote(MATERIAL, 100.0), PRODUCT: _quote(PRODUCT, 800.0)}
    best, build_cost, buy = _detail(home=home)
    assert build_cost == pytest.approx(1_000.0)
    assert buy == pytest.approx(800.0)
    assert best == pytest.approx(800.0)

    _setup_tech_ii(monkeypatch, _chosen(expected_cost_per_run=None))
    best, build_cost, buy = _detail(home=home)
    assert best == pytest.approx(500.0)  # without invention the build still wins


def test_invention_cost_per_unit_helper(monkeypatch):
    assert engine._invention_cost_per_unit(PRODUCT, 4.0, _chosen(800.0)) == pytest.approx(200.0)
    assert engine._invention_cost_per_unit(PRODUCT, 4.0, _chosen(None)) is None
    assert engine._invention_cost_per_unit(PRODUCT, 4.0, None) is None
    monkeypatch.setattr(storage, "get_manual_blueprint_copy_cost_per_run", lambda type_id: 1.0)
    assert engine._invention_cost_per_unit(PRODUCT, 4.0, _chosen(800.0)) is None


def test_build_list_row_carries_invention_cost_per_unit(monkeypatch):
    _setup_tech_ii(monkeypatch, _chosen(expected_cost_per_run=1_000.0))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 3600)
    cfg = ProductionConfig()
    home = {MATERIAL: _quote(MATERIAL, 100.0)}
    # _tech_ii_mods is stubbed, so fill the memo the way the real one does
    t2_memo = {PRODUCT: (1.0, 1.0, "None", _chosen(expected_cost_per_run=1_000.0))}
    cost_memo = {}
    engine._unit_cost(PRODUCT, cfg, home, {}, cost_memo, {}, t2_memo, {}, {})
    rows = engine._build_build_list({(200, 1, PRODUCT): 3}, cost_memo, t2_memo, cfg, home, {}, {})
    assert len(rows) == 1
    assert rows[0].invention_cost_per_unit == pytest.approx(500.0)
    assert rows[0].unit_build_cost == pytest.approx(1_000.0)


def test_build_list_row_without_invention_has_none(monkeypatch):
    _setup_single_material(monkeypatch, base_qty=10, product_qty=2.0)
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 3600)
    cfg = ProductionConfig()
    home = {MATERIAL: _quote(MATERIAL, 100.0)}
    cost_memo = {}
    engine._unit_cost(PRODUCT, cfg, home, {}, cost_memo, {}, {}, {}, {})
    rows = engine._build_build_list({(200, 1, PRODUCT): 3}, cost_memo, {}, cfg, home, {}, {})
    assert rows[0].invention_cost_per_unit is None

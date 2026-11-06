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

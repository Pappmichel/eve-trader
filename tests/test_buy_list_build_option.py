"""The "if built" fields on Buy List rows and the sell-side fields on Build
List rows, both shown in the row detail drawer."""

import pytest

from eve_trader import storage
from eve_trader.config import TRADING_CONFIG
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.models import BuyListEntry

PRODUCT = 100
MATERIAL = 300


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch):
    monkeypatch.setattr(TRADING_CONFIG, "jita_buy_broker_fee", 0.0)
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 4)
    monkeypatch.setattr(storage, "get_blueprint_skills", lambda blueprint_type_id, activity_id: [])
    monkeypatch.setattr(storage, "get_owned_bpo_best_me_te", lambda blueprint_id, **kwargs: None)
    monkeypatch.setattr(storage, "manual_bpo_best_me_te", lambda bp_type_id: None)
    monkeypatch.setattr(storage, "get_manual_blueprint_copy_cost_per_run", lambda type_id: None)
    monkeypatch.setattr(storage, "get_sde_type",
                        lambda type_id: (type_id, 958, f"Type {type_id}", 1.0, 1, 1125, 0, None))
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id", lambda blueprint_id: ())
    monkeypatch.setattr(engine, "shared_production_owner_ids", lambda data_kind: (None, None))
    monkeypatch.setattr(
        storage, "get_blueprint_for_product",
        lambda type_id: (200, 1, 1.0) if type_id == PRODUCT else None)
    monkeypatch.setattr(
        storage, "get_blueprint_materials",
        lambda blueprint_id, activity_id: [(MATERIAL, 3)] if blueprint_id == 200 else [])
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 3600)


def _quote(type_id, sell):
    return CurrentPrice(type_id=type_id, updated="2026-01-01", buy=sell * 0.9, sell=sell)


def _material_mult():
    mult, _, _ = engine._activity_mods("Tech I", PRODUCT, ProductionConfig(), {}, 200)
    return mult


def _entry(type_id, unit_price, **kwargs):
    return BuyListEntry(type_id=type_id, type_name=f"Type {type_id}", quantity=10.0,
                        unit_price=unit_price, total_price=None, **kwargs)


def _annotate(rows, home, manual_overrides=None, cost_memo=None):
    engine._annotate_build_option(
        rows, ProductionConfig(), home, {}, manual_overrides or {},
        {} if cost_memo is None else cost_memo, {}, {}, {}, {})
    return rows


def test_bought_because_buying_is_cheaper_carries_raw_build_cost():
    home = {MATERIAL: _quote(MATERIAL, 100.0), PRODUCT: _quote(PRODUCT, 200.0)}
    build_cost = 3 * _material_mult() * 100.0
    assert build_cost > 200.0
    # cost_memo holds min(buy, build) = the buy price; the row must not show that.
    [row] = _annotate([_entry(PRODUCT, 200.0)], home, cost_memo={PRODUCT: 200.0})
    assert row.buildable is True
    assert row.unit_build_cost == pytest.approx(build_cost)
    assert row.build_margin_home == pytest.approx(
        engine.margin_home(PRODUCT, build_cost, home, ProductionConfig()))
    assert row.buy_reason == "cheaper_to_buy"


def test_item_without_blueprint_is_not_buildable():
    [row] = _annotate([_entry(MATERIAL, 100.0)], {MATERIAL: _quote(MATERIAL, 100.0)})
    assert row.buildable is False
    assert row.unit_build_cost is None
    assert row.build_margin_home is None
    assert row.buy_reason == "no_blueprint"


def test_manual_buy_override_wins_over_cheaper_build():
    home = {MATERIAL: _quote(MATERIAL, 100.0), PRODUCT: _quote(PRODUCT, 10_000.0)}
    [row] = _annotate([_entry(PRODUCT, 10_000.0)], home, manual_overrides={PRODUCT: "Buy"})
    assert row.unit_build_cost < 10_000.0
    assert row.buy_reason == "manual"


def test_capacity_row_keeps_capacity_reason():
    home = {MATERIAL: _quote(MATERIAL, 100.0), PRODUCT: _quote(PRODUCT, 10_000.0)}
    [row] = _annotate([_entry(PRODUCT, 10_000.0, capacity_reason="capacity")], home)
    assert row.buy_reason == "capacity"
    assert row.unit_build_cost is not None


def test_missing_material_price_means_no_build_cost():
    [row] = _annotate([_entry(PRODUCT, 500.0)], {PRODUCT: _quote(PRODUCT, 500.0)})
    assert row.buildable is True
    assert row.unit_build_cost is None
    assert row.buy_reason == "no_build_cost"


def test_annotation_does_not_change_the_plans_memo():
    home = {MATERIAL: _quote(MATERIAL, 100.0), PRODUCT: _quote(PRODUCT, 200.0)}
    cost_memo = {PRODUCT: 200.0}
    _annotate([_entry(PRODUCT, 200.0)], home, cost_memo=cost_memo)
    assert cost_memo == {PRODUCT: 200.0}


def test_unannotated_row_has_no_build_fields():
    row = _entry(PRODUCT, 200.0)
    assert (row.buildable, row.unit_build_cost, row.build_margin_home, row.buy_reason) == (None, None, None, None)


def test_build_list_row_has_home_sell_and_profit_after_fees():
    cfg = ProductionConfig()
    home = {MATERIAL: _quote(MATERIAL, 100.0), PRODUCT: _quote(PRODUCT, 1_000.0)}
    cost_memo = {}
    engine._unit_cost(PRODUCT, cfg, home, {}, cost_memo, {}, {}, {}, {})
    [row] = engine._build_build_list({(200, 1, PRODUCT): 3}, cost_memo, {}, cfg, home, {}, {})
    assert row.home_sell_price == pytest.approx(1_000.0)
    assert row.profit_per_unit == pytest.approx(1_000.0 * (1 - cfg.market_fees) - row.unit_build_cost)


def test_build_list_row_without_home_quote_has_no_profit():
    cfg = ProductionConfig()
    home = {MATERIAL: _quote(MATERIAL, 100.0)}
    cost_memo = {}
    engine._unit_cost(PRODUCT, cfg, home, {}, cost_memo, {}, {}, {}, {})
    [row] = engine._build_build_list({(200, 1, PRODUCT): 3}, cost_memo, {}, cfg, home, {}, {})
    assert row.home_sell_price is None
    assert row.profit_per_unit is None

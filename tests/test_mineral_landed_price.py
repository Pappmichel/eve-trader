"""Effective landed price per mineral in both shopping lists' Coverage Check
(GitHub issue #420): price x delivered must add up to what the plan costs."""
import pytest

from eve_trader import mineral_supply_lp as supply
from eve_trader.api import schemas
from eve_trader.market_depth import SupplyTier
from eve_trader.module_reprocessing import models as module_models
from eve_trader.module_reprocessing.shopping_optimizer import optimize_shopping_list as optimize_modules
from eve_trader.refining.models import MineralOption, MineralRequirement, OreOption
from eve_trader.refining.optimizer import optimize_shopping_list

TRIT, MEX, PYE = 34, 36, 35


def _ore(type_id=1, cost=10.0, yields=None, tiers=None):
    return OreOption(type_id=type_id, item=f"Ore {type_id}", family="x", is_ice=False, volume_m3=0.15,
                     portion_size=100, landed_cost_per_unit=cost, yield_per_portion=yields or {TRIT: 400},
                     tiers=tiers)


def _mineral(type_id, price, tiers=None):
    return MineralOption(type_id=type_id, name=str(type_id), landed_cost_per_unit=price, tiers=tiers)


def _req(type_id, qty):
    return MineralRequirement(type_id=type_id, name=str(type_id), required_qty=qty)


def _prices(plan):
    return {c.type_id: c.landed_price_per_unit for c in plan.coverage}


def _assert_adds_up(plan):
    total = sum((c.landed_price_per_unit or 0.0) * c.delivered for c in plan.coverage)
    assert total == pytest.approx(plan.total_cost)


def test_direct_only_is_the_direct_price():
    plan = optimize_shopping_list([_req(TRIT, 1000)], [], {TRIT: _mineral(TRIT, 5.0)})
    assert _prices(plan)[TRIT] == pytest.approx(5.0)
    _assert_adds_up(plan)


def test_one_mineral_from_ore_carries_the_whole_ore_cost():
    # 1000 ISK per portion for 400 Tritanium.
    plan = optimize_shopping_list([_req(TRIT, 4000)], [_ore()], {TRIT: _mineral(TRIT, 6.0)})
    assert _prices(plan)[TRIT] == pytest.approx(2.5)
    _assert_adds_up(plan)


def test_joint_ore_cost_is_split_by_reference_value():
    # 400 Tritanium at reference 5 (2000) and 100 Mexallon at reference 100
    # (10000): Tritanium takes 1/6 of the 1000 ISK portion.
    plan = optimize_shopping_list(
        [_req(TRIT, 400), _req(MEX, 100)], [_ore(yields={TRIT: 400, MEX: 100})],
        {TRIT: _mineral(TRIT, 5.0), MEX: _mineral(MEX, 100.0)})
    prices = _prices(plan)
    assert prices[TRIT] == pytest.approx(1000 / 6 / 400)
    assert prices[MEX] == pytest.approx(1000 * 5 / 6 / 100)
    _assert_adds_up(plan)


def test_mineral_not_on_the_list_takes_no_share():
    plan = optimize_shopping_list([_req(TRIT, 400)], [_ore(yields={TRIT: 400, PYE: 1000})],
                                  {TRIT: _mineral(TRIT, 6.0)})
    assert _prices(plan)[TRIT] == pytest.approx(2.5)
    assert set(_prices(plan)) == {TRIT}


def test_mean_of_two_sources_is_what_was_paid_not_the_larger_source():
    # Only one portion is listed: 120 ISK for 40 units (3 ISK each), the other
    # 60 units cost 5 ISK directly. A median would say 5, the plan pays 4.2.
    tiers = [SupplyTier(unit_cost=1.2, max_units=100)]
    plan = optimize_shopping_list([_req(TRIT, 100)],
                                  [_ore(cost=1.2, yields={TRIT: 40}, tiers=tiers)],
                                  {TRIT: _mineral(TRIT, 5.0)})
    assert _prices(plan)[TRIT] == pytest.approx(4.2)
    _assert_adds_up(plan)


def test_surplus_is_spread_over_delivered_units():
    plan = optimize_shopping_list([_req(TRIT, 450)], [_ore()], {TRIT: _mineral(TRIT, 100.0)})
    coverage = plan.coverage[0]
    assert coverage.delivered == 800
    assert coverage.landed_price_per_unit == pytest.approx(2000 / 800)
    _assert_adds_up(plan)


def test_nothing_delivered_has_no_price():
    plan = optimize_shopping_list([_req(TRIT, 100), _req(MEX, 50)], [], {TRIT: _mineral(TRIT, 5.0)})
    prices = _prices(plan)
    assert prices[MEX] is None
    assert prices[TRIT] == pytest.approx(5.0)
    _assert_adds_up(plan)


def test_without_a_reference_price_the_source_splits_by_quantity():
    # Mexallon cannot be bought directly at all: 400 + 100 units share the cost.
    plan = optimize_shopping_list(
        [_req(TRIT, 400), _req(MEX, 100)], [_ore(yields={TRIT: 400, MEX: 100})],
        {TRIT: _mineral(TRIT, 5.0)})
    prices = _prices(plan)
    assert prices[TRIT] == pytest.approx(2.0)
    assert prices[MEX] == pytest.approx(2.0)
    _assert_adds_up(plan)


def test_depth_tiers_use_the_price_actually_paid():
    tiers = [SupplyTier(unit_cost=2.0, max_units=100), SupplyTier(unit_cost=3.0, max_units=100)]
    plan = optimize_shopping_list([_req(TRIT, 800)], [_ore(cost=2.0, tiers=tiers)], {TRIT: _mineral(TRIT, 100.0)})
    assert _prices(plan)[TRIT] == pytest.approx((200 + 300) / 800)
    _assert_adds_up(plan)


def test_rounded_lp_plan_still_adds_up(monkeypatch):
    monkeypatch.setattr(supply, "_run_mip", lambda kwargs: None)
    plan = optimize_shopping_list(
        [_req(TRIT, 1234), _req(MEX, 77)], [_ore(yields={TRIT: 400, MEX: 50})],
        {TRIT: _mineral(TRIT, 6.0), MEX: _mineral(MEX, 50.0)})
    assert plan.approximate
    _assert_adds_up(plan)


def test_module_shopping_list_reports_the_same_figure():
    option = module_models.ReprocessOption(
        type_id=1, item="Module", category="module", family=None, is_ice=False, volume_m3=5.0, portion_size=1,
        landed_cost_per_unit=1000.0, yield_per_portion={TRIT: 400, MEX: 100})
    plan = optimize_modules(
        [module_models.MineralRequirement(type_id=TRIT, name="T", required_qty=400),
         module_models.MineralRequirement(type_id=MEX, name="M", required_qty=100)],
        [option],
        {TRIT: module_models.MineralOption(type_id=TRIT, name="T", landed_cost_per_unit=5.0),
         MEX: module_models.MineralOption(type_id=MEX, name="M", landed_cost_per_unit=100.0)})
    assert {c.type_id: c.landed_price_per_unit for c in plan.coverage}[TRIT] == pytest.approx(1000 / 6 / 400)
    total = sum(c.landed_price_per_unit * c.delivered for c in plan.coverage)
    assert total == pytest.approx(plan.total_cost)


@pytest.mark.parametrize("schema, key", [(schemas.MineralCoverage, "from_ore"),
                                         (schemas.ReprocessMineralCoverage, "from_reprocessing")])
def test_response_schemas_keep_the_price(schema, key):
    row = schema(type_id=34, name="T", required=1.0, from_direct=1, delivered=1, surplus=0.0,
                 landed_price_per_unit=3.25, **{key: 0})
    assert row.landed_price_per_unit == 3.25

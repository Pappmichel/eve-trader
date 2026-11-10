"""import_planner: the Import tab's buy list under an ISK and/or m3 limit."""
import pytest

from eve_trader import import_planner as ip
from eve_trader.import_planner import ImportSource, ImportTier, plan_import

TRIT, PYE = 34, 35


def _source(type_id=1, net=10.0, tiers=((5.0, 100),), volume=1.0, portion=1, yields=None, category="ore"):
    return ImportSource(
        type_id=type_id, item=f"Item {type_id}", category=category, hub_region_id=10000002,
        portion_size=portion, volume_m3=volume, net_sell_per_unit=net,
        yield_per_portion=yields or {TRIT: 10},
        tiers=[ImportTier(hub_price=cost, unit_cost=cost, max_units=units) for cost, units in tiers])


def test_needs_at_least_one_limit():
    with pytest.raises(ip.ImportPlanError):
        plan_import([_source()])


def test_rejects_non_positive_limits():
    with pytest.raises(ip.ImportPlanError):
        plan_import([_source()], budget_isk=0)
    with pytest.raises(ip.ImportPlanError):
        plan_import([_source()], volume_m3=-1)


def test_budget_binds_and_best_margin_goes_first():
    good = _source(1, net=10.0, tiers=((5.0, 100),))     # +5 per 5 ISK
    worse = _source(2, net=10.0, tiers=((8.0, 100),))    # +2 per 8 ISK
    plan = plan_import([good, worse], budget_isk=600.0)
    by_id = {line.type_id: line for line in plan.lines}
    assert by_id[1].units == 100
    assert by_id[2].units == 12  # 100 ISK left / 8
    assert plan.total_cost <= 600.0
    assert plan.limited_by == ip.LIMITED_BY_BUDGET


def test_volume_binds_and_prefers_profit_per_m3():
    dense = _source(1, net=10.0, tiers=((5.0, 100),), volume=1.0)    # 5 / m3
    bulky = _source(2, net=30.0, tiers=((10.0, 100),), volume=10.0)  # 2 / m3
    plan = plan_import([dense, bulky], volume_m3=150.0)
    by_id = {line.type_id: line for line in plan.lines}
    assert by_id[1].units == 100
    assert by_id[2].units == 5
    assert plan.total_volume_m3 == pytest.approx(150.0)
    assert plan.limited_by == ip.LIMITED_BY_VOLUME


def test_both_limits_hold():
    a = _source(1, net=10.0, tiers=((5.0, 1000),), volume=1.0)
    plan = plan_import([a], budget_isk=10_000.0, volume_m3=50.0)
    assert plan.lines[0].units == 50
    assert plan.limited_by == ip.LIMITED_BY_VOLUME


def test_deeper_tiers_only_while_profitable_and_supply_limits():
    src = _source(1, net=10.0, tiers=((5.0, 10), (9.0, 10), (11.0, 10)))
    plan = plan_import([src], budget_isk=1e9)
    line = plan.lines[0]
    assert line.units == 20
    assert line.cost == pytest.approx(140.0)
    assert line.max_unit_cost == 9.0
    assert line.units_within_cap == 20
    assert plan.limited_by == ip.LIMITED_BY_SUPPLY


def test_whole_portions_with_top_up():
    # Portion of 100 units; budget fits 2.5 portions of the first item, the
    # remainder buys a single-unit item.
    big = _source(1, net=10.0, tiers=((5.0, 1000),), portion=100)
    small = _source(2, net=6.0, tiers=((5.0, 1000),), portion=1)
    plan = plan_import([big, small], budget_isk=1250.0)
    by_id = {line.type_id: line for line in plan.lines}
    assert by_id[1].units % 100 == 0
    assert by_id[1].portions == 2
    assert by_id[2].units == 50
    assert plan.total_cost == pytest.approx(1250.0)


def test_never_buys_part_of_a_portion_beyond_supply():
    src = _source(1, net=10.0, tiers=((5.0, 150),), portion=100)
    plan = plan_import([src], budget_isk=1e9)
    assert plan.lines[0].units == 100


def test_minerals_and_home_listed_warning():
    src = _source(1, net=10.0, tiers=((5.0, 10),), yields={TRIT: 100, PYE: 5})
    plan = plan_import([src], budget_isk=1e9, mineral_unit_values={TRIT: 0.1, PYE: 1.0},
                       mineral_names={TRIT: "Tritanium", PYE: "Pyerite"}, home_listed={TRIT: 500.0, PYE: None})
    minerals = {m.type_id: m for m in plan.minerals}
    assert minerals[TRIT].quantity == 1000
    assert minerals[TRIT].value == pytest.approx(100.0)
    assert minerals[TRIT].exceeds_listed
    assert not minerals[PYE].exceeds_listed
    assert plan.total_mineral_value == pytest.approx(150.0)


def test_unpriced_mineral_leaves_total_unknown():
    src = _source(1, tiers=((5.0, 10),), yields={TRIT: 1})
    plan = plan_import([src], budget_isk=1e9, mineral_unit_values={TRIT: None})
    assert plan.total_mineral_value is None


def test_nothing_profitable_gives_an_empty_plan():
    plan = plan_import([_source(1, net=4.0, tiers=((5.0, 10),))], budget_isk=100.0)
    assert plan.lines == []
    assert plan.total_profit == 0
    assert plan.limited_by == ip.LIMITED_BY_SUPPLY


def test_totals_add_up():
    plan = plan_import([_source(1), _source(2, net=7.0)], budget_isk=700.0)
    assert plan.total_profit == pytest.approx(sum(line.profit for line in plan.lines))
    assert plan.margin == pytest.approx(plan.total_profit / plan.total_cost)


def test_cap_hub_price_meets_margin_and_profit():
    # net 110/unit, 1 m3 at 10 ISK/m3, 0% broker fee, 10% margin: landed <= 100
    assert ip.cap_hub_price(110.0, 1.0, 10.0, 0.0, 0.10, 0.0) == pytest.approx(90.0)
    # a min profit of 50/unit is the stricter bar: landed <= 60
    assert ip.cap_hub_price(110.0, 1.0, 10.0, 0.0, 0.10, 50.0) == pytest.approx(50.0)
    assert ip.cap_hub_price(5.0, 1.0, 10.0, 0.0, 0.0, 0.0) is None


def test_build_tiers_uses_the_cap_not_the_tolerance():
    orders = [{"price": p, "volume_remain": 10, "is_buy_order": False} for p in (100.0, 120.0, 150.0)]
    tiers = ip.build_tiers(orders, 100.0, 130.0, lambda price: price + 1)
    assert sum(t.max_units for t in tiers) == 20
    assert tiers[0].unit_cost == 101.0
    assert ip.build_tiers(orders, 100.0, None, lambda price: price) == []

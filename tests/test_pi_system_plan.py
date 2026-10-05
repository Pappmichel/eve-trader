"""Tests for eve_trader/pi/system_plan.py (pure MILP system plan)."""
from __future__ import annotations

import pytest

from eve_trader.pi import system_plan as sp
from eve_trader.pi.system_plan import Market, Option, plan_system

P1, P2, P3X = 2393, 3689, 9999

# Hand-checked market used by most tests:
#   P1: sell 20, freight 5 -> net sale 15/unit; buy 30 + freight 5 -> 35/unit
#   P2: sell 400, freight 10 -> net sale 390/unit; not buyable
MARKET = Market(
    sell_value={P1: 20.0, P2: 400.0},
    buy_cost={P1: 30.0},
    freight_per_unit={P1: 5.0, P2: 10.0},
)

# Extraction colony on planet 1: 100 P1/day, 1000 ISK/day customs+setup.
EXT = Option("1:P0-P1:P1", 1, "P0-P1", P1, True, {P1: 100.0}, {}, 1000.0, "P1 extraction")
# Factory colony on planet 2: 100 P1/day in, 20 P2/day out, 2000 ISK/day.
FAC = Option("2:P1-P2:P2", 2, "P1-P2", P2, False, {P2: 20.0}, {P1: 100.0}, 2000.0, "P2 factory")


def _flow(plan, type_id):
    return next(f for f in plan.flows if f.type_id == type_id)


def _count(plan, key):
    return sum(c.count for c in plan.colonies if c.key == key)


def test_in_system_chain_beats_best_single_uses_hand_checked():
    # Stand-alone: EXT = 100*15 - 1000 = 500; FAC = 20*390 - 100*35 - 2000 = 2300.
    # Chain EXT+FAC (P1 internal, no freight/market): 20*390 - 1000 - 2000 = 4800.
    # characters=1: best single uses = FAC (2300) + EXT (500) = 2800 (FAC cannot
    # repeat on planet 2), gain 2000.
    plan = plan_system([EXT, FAC], MARKET, slots=2, characters=1)
    assert plan.status == "optimal"
    assert _count(plan, EXT.key) == 1 and _count(plan, FAC.key) == 1
    assert _flow(plan, P1).internal == pytest.approx(100.0)
    assert _flow(plan, P1).bought == pytest.approx(0.0)
    assert _flow(plan, P2).sold == pytest.approx(20.0)
    assert plan.profit_per_day == pytest.approx(4800.0)
    assert plan.profit_per_slot == pytest.approx(2400.0)
    assert plan.best_single_uses_profit_per_day == pytest.approx(2800.0)
    assert plan.chain_gain_per_day == pytest.approx(2000.0)

    # characters=2: two stand-alone factories on planet 2 = 4600 < chain 4800.
    plan2 = plan_system([EXT, FAC], MARKET, slots=2, characters=2)
    assert plan2.profit_per_day == pytest.approx(4800.0)
    assert plan2.best_single_uses_profit_per_day == pytest.approx(4600.0)


def test_slots_limit_respected():
    plan = plan_system([EXT, FAC], MARKET, slots=1, characters=3)
    assert plan.used_slots == 1
    assert sum(c.count for c in plan.colonies) == 1
    assert _count(plan, FAC.key) == 1          # 2300 beats 500
    assert plan.profit_per_day == pytest.approx(2300.0)


def test_per_planet_character_limit():
    fac_b = Option("2:P1-P2:P2b", 2, "P1-P2", P2, False, {P2: 20.0}, {P1: 100.0}, 2000.0)
    plan = plan_system([EXT, FAC, fac_b], MARKET, slots=10, characters=1)
    per_planet: dict[int, int] = {}
    for c in plan.colonies:
        per_planet[c.planet_id] = per_planet.get(c.planet_id, 0) + c.count
    assert all(n <= 1 for n in per_planet.values())
    assert plan.used_slots == 2


def test_extraction_repeat_penalty_reduces_second_colony():
    # Only extraction on planet 1, 2 characters: 100 + 0.85*100 = 185 P1 sold,
    # profit 185*15 - 2*1000 = 775 (fixed cost not scaled by default).
    plan = plan_system([EXT], MARKET, slots=2, characters=2)
    assert _count(plan, EXT.key) == 2
    col = plan.colonies[0]
    assert col.yield_factors == pytest.approx((1.0, 0.85))
    assert _flow(plan, P1).produced == pytest.approx(185.0)
    assert plan.profit_per_day == pytest.approx(775.0)
    assert any("yield penalty" in n for n in plan.notes)

    # Penalty is per planet across all extraction options: a second, different
    # extraction option on the same planet also takes rank 2.
    other = Option("1:P0-P1:other", 1, "P0-P1", P1, True, {P1: 100.0}, {}, 1000.0)
    plan_b = plan_system([EXT, other], MARKET, slots=2, characters=2)
    assert _flow(plan_b, P1).produced == pytest.approx(185.0)

    # setup_cost_per_day given: the customs part of rank 2 scales too.
    # rank 2 fixed = 400 + 600*0.85 = 910 -> profit 185*15 - 1000 - 910 = 865.
    ext_s = Option(EXT.key, 1, "P0-P1", P1, True, {P1: 100.0}, {}, 1000.0, setup_cost_per_day=400.0)
    assert plan_system([ext_s], MARKET, 2, 2).profit_per_day == pytest.approx(865.0)

    # max_extraction_per_planet=1 forbids the second one.
    plan_c = plan_system([EXT], MARKET, slots=2, characters=2, max_extraction_per_planet=1)
    assert _count(plan_c, EXT.key) == 1


def test_unsellable_output_only_built_when_consumed_internally():
    unsellable = Option("3:P0-P1:X", 3, "P0-P1", P3X, True, {P3X: 50.0}, {}, 100.0)
    plan = plan_system([unsellable], MARKET, slots=3, characters=1)
    assert plan.used_slots == 0
    assert plan.profit_per_day == 0.0

    # A factory consuming it (X not buyable, so only in-system supply works):
    # 10 P2 * 390 - 100 - 500 = 3300.
    consumer = Option("4:P1-P2:P2", 4, "P1-P2", P2, False, {P2: 10.0}, {P3X: 50.0}, 500.0)
    plan2 = plan_system([unsellable, consumer], MARKET, slots=3, characters=1)
    assert _count(plan2, unsellable.key) == 1 and _count(plan2, consumer.key) == 1
    assert plan2.profit_per_day == pytest.approx(3300.0)
    # Stand-alone the consumer cannot be supplied at all.
    assert plan2.best_single_uses_profit_per_day == 0.0


def test_max_sell_cap_binds():
    # fixed 100: rank 1 = 100*15 - 100 = 1400; rank 2 adds min(85, 150-100)*15
    # - 100 = 650; rank 3 sells nothing more (-100) -> 2 colonies, 2050.
    ext = Option("1:P0-P1:P1", 1, "P0-P1", P1, True, {P1: 100.0}, {}, 100.0)
    market = Market(MARKET.sell_value, MARKET.buy_cost, MARKET.freight_per_unit,
                    max_sell_per_day={P1: 150.0})
    plan = plan_system([ext], market, slots=3, characters=3)
    assert _count(plan, ext.key) == 2
    f = _flow(plan, P1)
    assert f.sold == pytest.approx(150.0)
    assert f.discarded == pytest.approx(35.0)
    assert plan.profit_per_day == pytest.approx(2050.0)


def test_fallback_when_solver_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("highs exploded")

    monkeypatch.setattr(sp, "linprog", boom)
    plan = plan_system([EXT, FAC], MARKET, slots=2, characters=1)
    assert plan.status == "fallback"
    assert any("greedy" in n for n in plan.notes)
    # Greedy: FAC first (2300), then EXT marginal 4800 - 2300 = 2500.
    assert plan.profit_per_day == pytest.approx(4800.0)


def test_fallback_when_solver_returns_no_solution(monkeypatch):
    class R:
        x = None
        status = 2
        message = "infeasible"

    monkeypatch.setattr(sp, "linprog", lambda *a, **k: R())
    plan = plan_system([EXT], MARKET, slots=1, characters=1)
    assert plan.status == "fallback"
    assert plan.profit_per_day == pytest.approx(500.0)


def test_empty_inputs():
    assert plan_system([], MARKET, slots=5, characters=1).status == "infeasible"
    plan = plan_system([EXT], MARKET, slots=0, characters=1)
    assert plan.status == "infeasible" and plan.colonies == [] and plan.notes
    assert plan_system([EXT], MARKET, slots=3, characters=0).status == "infeasible"


def test_malformed_input_raises():
    with pytest.raises(ValueError):
        plan_system([EXT], MARKET, slots=-1, characters=1)
    with pytest.raises(ValueError):
        plan_system([EXT, EXT], MARKET, slots=1, characters=1)


def test_realistic_size_solves():
    # ~20 planets x 12 options; must stay well under the time limit.
    options = []
    for p in range(20):
        for j in range(6):
            options.append(Option(f"{p}:e{j}", p, "P0-P1", P1, True,
                                  {P1: 80.0 + 7 * j + p}, {}, 900.0 + 13 * j))
            options.append(Option(f"{p}:f{j}", p, "P1-P2", P2, False,
                                  {P2: 15.0 + j}, {P1: 90.0 + 5 * j}, 1800.0 + 50 * p))
    plan = plan_system(options, MARKET, slots=30, characters=3)
    assert plan.status in ("optimal", "time_limit")
    assert plan.used_slots <= 30
    assert plan.profit_per_day >= plan.best_single_uses_profit_per_day - 1e-6

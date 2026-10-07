"""Chain planner (eve_trader/pi/chain_plan.py, pure) and its action
(chain_actions.py, I/O helpers monkeypatched). The static rows are the real
SDE snapshot (tests/fixtures/pi_static_rows.json); prices are synthetic."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from eve_trader.actions import ActionError
from eve_trader.pi import actions, chain_actions, engine, static
from eve_trader.pi import chain_plan as cp
from eve_trader.pi import economics as econ
from eve_trader.pi.config import PiConfig
from eve_trader.pi.model import Planet
from eve_trader.pi.system_plan import Market

_ROWS = json.loads((Path(__file__).parent / "fixtures" / "pi_static_rows.json").read_text(encoding="utf-8"))

MECHANICAL_PARTS, REACTIVE_METALS, PRECIOUS_METALS = 3689, 2398, 2399
BROADCAST_NODE = 2867
BACTERIA = 2393
BARREN, LAVA, GAS, PLASMA = 2016, 2015, 13, 2063

_BASE = {0: 2.0, 1: 400.0, 2: 9000.0, 3: 60000.0, 4: 1_200_000.0}


@pytest.fixture(scope="module")
def sd():
    return static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})


@pytest.fixture(autouse=True)
def _clear_design_cache():
    engine.clear_cache()
    yield
    engine.clear_cache()


def _prices(sd):
    return econ.Prices(sell={t: _BASE[c.tier] for t, c in sd.commodities.items()},
                       buy={t: _BASE[c.tier] * 0.9 for t, c in sd.commodities.items()})


def _settings():
    return econ.MarketSettings(broker_fee=0.03, sales_tax=0.036, valuation="sell_orders", freight_per_m3=500,
                               tax_rate=0.05, amortisation_days=30, min_isk_per_planet_day=0,
                               market_share_warning=0.1, program_hours=72, interval_hours=24)


def _plan(sd, target, planets, chars, **kw):
    opts = cp.build_options(sd, target, planets, sorted({c.cc_level for c in chars}), engine.Assumptions(),
                            _prices(sd), _settings(), factory_planet_limit=max(c.planets for c in chars))
    return cp.plan_chain(sd, target, planets, chars, opts, cp.build_market(sd, _prices(sd), _settings()), **kw)


def _assert_limits(plan, chars):
    per_char = Counter(a.character_key for a in plan.assignments)
    per_char_planet = Counter((a.character_key, a.planet_id) for a in plan.assignments)
    by_key = {c.key: c for c in chars}
    for key, n in per_char.items():
        assert n <= by_key[key].planets
    assert all(n == 1 for n in per_char_planet.values())           # one CC per character per planet
    assert plan.used_slots == len(plan.assignments) <= sum(c.planets for c in chars)
    for a in plan.assignments:
        assert a.cc_level == by_key[a.character_key].cc_level
        assert a.design["cc_level"] == a.cc_level                   # design for the character's own level


# ------------------------------------------------------------ real designs
def test_p2_chain_in_a_system_with_both_p0s(sd):
    planets = [Planet(BARREN, 4000, 1, "B I"), Planet(LAVA, 6000, 2, "L II"), Planet(GAS, 30000, 3, "G III"),
               Planet(PLASMA, 5000, 4, "P IV")]
    chars = [cp.CharacterSpec("a", "A", 3, 4), cp.CharacterSpec("b", "B", 3, 5)]
    plan = _plan(sd, MECHANICAL_PARTS, planets, chars, allow_buy=False)
    assert plan.status == "optimal" and plan.mode == "target"
    assert plan.target_units_per_day > 0
    assert plan.purchases == []                                      # everything made in the system
    assert any(a.is_extraction for a in plan.assignments)
    stages = {s.type_id: s for s in plan.stages}
    # Own P1 feeds a factory colony (or the P1 half of a P0->P2 colony).
    assert stages[REACTIVE_METALS].internal > 0 or stages[PRECIOUS_METALS].internal > 0
    assert stages[MECHANICAL_PARTS].sold == pytest.approx(plan.target_units_per_day)
    _assert_limits(plan, chars)
    assert {a.cc_level for a in plan.assignments} <= {4, 5}


def test_missing_p0_is_bought_and_reported(sd):
    # Lava and Gas carry Base Metals but no Noble Metals.
    planets = [Planet(LAVA, 6000, 2, "L II"), Planet(GAS, 30000, 3, "G III")]
    chars = [cp.CharacterSpec("a", "A", 2, 5)]
    plan = _plan(sd, MECHANICAL_PARTS, planets, chars)
    assert plan.target_units_per_day > 0
    bought = {p.type_id: p for p in plan.purchases}
    assert PRECIOUS_METALS in bought
    assert bought[PRECIOUS_METALS].reason_code == "no_resource"
    assert "Noble Metals is on no planet" in bought[PRECIOUS_METALS].reason
    _assert_limits(plan, chars)


def test_p4_without_barren_or_temperate_makes_the_inputs(sd):
    planets = [Planet(LAVA, 6000, 2, "L II"), Planet(GAS, 30000, 3, "G III"), Planet(PLASMA, 5000, 4, "P IV")]
    chars = [cp.CharacterSpec("a", "A", 3, 5)]
    plan = _plan(sd, BROADCAST_NODE, planets, chars)
    assert any("No Barren or Temperate" in n for n in plan.notes)
    assert plan.mode == "inputs"
    assert BROADCAST_NODE not in plan.top_type_ids
    assert all(sd.tier(t) == 3 for t in plan.top_type_ids)
    assert all(a.product_type_id != BROADCAST_NODE for a in plan.assignments)
    assert plan.target_units_per_day > 0
    _assert_limits(plan, chars)


def test_more_slots_never_make_less_target(sd):
    planets = [Planet(BARREN, 4000, 1, "B I"), Planet(LAVA, 6000, 2, "L II"), Planet(PLASMA, 5000, 4, "P IV")]
    results = []
    for chars in ([cp.CharacterSpec("a", "A", 1, 5)],
                  [cp.CharacterSpec("a", "A", 3, 5)],
                  [cp.CharacterSpec("a", "A", 3, 5), cp.CharacterSpec("b", "B", 3, 5)]):
        plan = _plan(sd, MECHANICAL_PARTS, planets, chars, allow_buy=False)
        _assert_limits(plan, chars)
        results.append(plan.target_units_per_day)
    assert results[0] > 0
    assert results[0] <= results[1] + 1e-6 <= results[2] + 2e-6


# ------------------------------------------------------- hand-checked case
PLANETS3 = [Planet(BARREN, 4000, 1, "one"), Planet(BARREN, 4000, 2, "two"), Planet(BARREN, 4000, 3, "three")]
MARKET = Market(sell_value={MECHANICAL_PARTS: 1000.0, REACTIVE_METALS: 5.0, PRECIOUS_METALS: 5.0},
                buy_cost={REACTIVE_METALS: 10.0, PRECIOUS_METALS: 10.0}, freight_per_unit={})


def _hand_options(rm_per_day=1000.0):
    return [
        cp.ChainOption("1:rm", 1, 4, "P0-P1", REACTIVE_METALS, True, {REACTIVE_METALS: rm_per_day}, {}, 100.0, 50.0),
        cp.ChainOption("2:pm", 2, 4, "P0-P1", PRECIOUS_METALS, True, {PRECIOUS_METALS: 1000.0}, {}, 100.0, 50.0),
        cp.ChainOption("3:fac", 3, 4, "P1-P2", MECHANICAL_PARTS, False, {MECHANICAL_PARTS: 25.0},
                       {REACTIVE_METALS: 1000.0, PRECIOUS_METALS: 1000.0}, 200.0, 100.0),
    ]


def test_hand_checked_full_chain(sd):
    # Three slots: both extraction colonies feed the factory, nothing is bought.
    # profit = 25 * 1000 - (100+50) - (100+50) - (200+100) = 24,400 ISK/day.
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, PLANETS3, [cp.CharacterSpec("a", "A", 3, 4)], _hand_options(), MARKET)
    assert plan.status == "optimal"
    assert plan.target_units_per_day == pytest.approx(25.0, rel=1e-4)
    assert plan.profit_per_day == pytest.approx(24400.0, rel=1e-4)
    assert plan.purchases == [] and plan.used_slots == 3 and plan.free_slots == 0


def test_hand_checked_two_slots_buy_the_missing_p1(sd):
    # Two slots cannot hold the whole chain, so phase A repeats with purchases
    # allowed: factory + one extraction colony, the other P1 is bought.
    # profit = 25 * 1000 - 300 - 150 - 1000 * 10 = 14,550 ISK/day.
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, PLANETS3, [cp.CharacterSpec("a", "A", 2, 4)], _hand_options(), MARKET)
    assert plan.target_units_per_day == pytest.approx(25.0, rel=1e-4)
    assert plan.profit_per_day == pytest.approx(14550.0, rel=1e-4)
    assert len(plan.purchases) == 1 and plan.purchases[0].units_per_day == pytest.approx(1000.0, rel=1e-4)
    assert plan.purchases[0].reason_code == "no_capacity"
    assert any("buys part of the inputs" in n for n in plan.notes)


def test_hand_checked_partial_factory_and_allow_buy(sd):
    # Only 500 RM/day from own extraction. Without purchases the factory runs
    # at 50%: 12.5 units; setup 200 + half the customs 50; the 500 unused PM
    # are sold at 5. profit = 12,500 - 250 - 150 - 150 + 2,500 = 14,450.
    chars = [cp.CharacterSpec("a", "A", 3, 4)]
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, PLANETS3, chars, _hand_options(500.0), MARKET, allow_buy=False)
    assert plan.target_units_per_day == pytest.approx(12.5, rel=1e-4)
    assert plan.profit_per_day == pytest.approx(14450.0, rel=1e-4)
    fac = next(a for a in plan.assignments if not a.is_extraction)
    assert fac.run_level == pytest.approx(0.5, rel=1e-4)
    # With purchases allowed, buying the missing 500 RM (5,000) to run the
    # factory full (+12,500 sales, +50 customs, -2,500 PM no longer sold) pays:
    # profit = 25,000 - 300 - 150 - 150 - 5,000 = 19,400.
    plan2 = cp.plan_chain(sd, MECHANICAL_PARTS, PLANETS3, chars, _hand_options(500.0), MARKET, allow_buy=True)
    assert plan2.target_units_per_day == pytest.approx(25.0, rel=1e-4)
    assert plan2.profit_per_day == pytest.approx(19400.0, rel=1e-4)
    assert [p.reason_code for p in plan2.purchases] == ["no_capacity"]   # all 3 slots are used


def test_no_options_or_characters_is_a_plan_not_an_error(sd):
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, PLANETS3, [], _hand_options(), MARKET)
    assert plan.status == "infeasible" and plan.assignments == []
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, PLANETS3, [cp.CharacterSpec("a", "A", 3, 2)], _hand_options(), MARKET)
    assert plan.status == "infeasible"     # options exist only for CC 4


def test_greedy_fallback_when_the_solver_fails(sd, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cp, "linprog", broken)
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, PLANETS3, [cp.CharacterSpec("a", "A", 3, 4)], _hand_options(), MARKET)
    assert plan.status == "fallback"
    assert plan.target_units_per_day > 0
    assert any("greedy" in n for n in plan.notes)


@pytest.mark.parametrize("kwargs", [
    {"target": 2073},                                                        # a P0
    {"target": 1},                                                           # unknown
    {"chars": [cp.CharacterSpec("a", "A", 0, 4)]},
    {"chars": [cp.CharacterSpec("a", "A", 7, 4)]},
    {"chars": [cp.CharacterSpec("a", "A", 3, 9)]},
    {"chars": [cp.CharacterSpec("a", "A", 3, 4), cp.CharacterSpec("a", "B", 3, 4)]},
    {"options": _hand_options() + [cp.ChainOption("9:x", 9, 4, "P0-P1", BACTERIA, True, {BACTERIA: 1.0}, {}, 0, 0)]},
    {"options": _hand_options() + [_hand_options()[0]]},
    {"options": [cp.ChainOption("1:neg", 1, 4, "P0-P1", BACTERIA, True, {BACTERIA: -1.0}, {}, 0, 0)]},
])
def test_malformed_input_raises(sd, kwargs):
    with pytest.raises(ValueError):
        cp.plan_chain(sd, kwargs.get("target", MECHANICAL_PARTS), PLANETS3,
                      kwargs.get("chars", [cp.CharacterSpec("a", "A", 3, 4)]),
                      kwargs.get("options", _hand_options()), MARKET)


# ------------------------------------------------------------ the action
@pytest.fixture
def patched(sd, monkeypatch):
    monkeypatch.setattr(actions, "_static", lambda: sd)
    monkeypatch.setattr(actions, "_prices", lambda static_, cfg, with_history=True: _prices(sd))
    monkeypatch.setattr(actions, "_assumptions", lambda cfg, zone, *a, **k: engine.Assumptions())
    monkeypatch.setattr(actions, "_radius_medians", lambda: {})
    monkeypatch.setattr(actions, "do_characters", lambda cfg: {"characters": []})
    monkeypatch.setattr(actions, "do_system_planets", lambda sid: {
        "solar_system_id": sid, "name": "Test", "security": 0.9, "region_id": 10000002, "zone": "highsec",
        "reachable": True,
        "planets": [{"planet_id": 11, "name": "Test I", "planet_type_id": BARREN, "planet_type": "Barren",
                     "radius_km": 4000.0},
                    {"planet_id": 12, "name": "Test II", "planet_type_id": PLASMA, "planet_type": "Plasma",
                     "radius_km": 5000.0}],
    })


def test_action_system_plan_with_manual_characters(patched):
    cfg = PiConfig(pi_characters=2, pi_planets_per_character=2, pi_cc_level=4)
    out = chain_actions.do_chain_plan(MECHANICAL_PARTS, solar_system_id=30000142, cfg=cfg)
    assert out["target_name"] == "Mechanical Parts" and out["zone"] == "highsec"
    assert [c["name"] for c in out["characters"]] == ["Character 1", "Character 2"]
    assert out["assignments"] and out["target_units_per_day"] > 0
    first = out["assignments"][0]
    assert first["layout_request"]["planet_id"] in (11, 12)
    assert first["layout_request"]["design"]["cc_level"] == 4
    json.dumps(out)                                                       # JSON-friendly


def test_action_generic_plan_and_given_characters(patched):
    out = chain_actions.do_chain_plan(MECHANICAL_PARTS, characters=[{"name": "Main", "planets": 3, "cc_level": 5}],
                                      cfg=PiConfig())
    assert out["system"] is None
    assert [c["name"] for c in out["characters"]] == ["Main"]
    assert all(a["planet_id"] is None and a["layout_request"]["planet_type_id"] for a in out["assignments"])
    assert any("No system chosen" in n for n in out["notes"])


def test_action_errors(patched):
    with pytest.raises(ActionError):
        chain_actions.do_chain_plan(2073, cfg=PiConfig())                  # P0
    with pytest.raises(ActionError):
        chain_actions.do_chain_plan(MECHANICAL_PARTS, characters=[{"name": "x", "planets": 9, "cc_level": 5}],
                                    cfg=PiConfig())
    with pytest.raises(ActionError, match="per hour"):
        chain_actions.do_chain_plan(MECHANICAL_PARTS, target_per_hour=0, cfg=PiConfig())
    with pytest.raises(ActionError, match="per hour"):
        chain_actions.do_chain_plan(MECHANICAL_PARTS, target_per_hour=1_000_001, cfg=PiConfig())


# ------------------------------------------------ requested output rate
def _rate_options():
    """Two full chains: each factory needs 1000 of each P1 per 25 units/day,
    and each extractor makes 1000/day. Six planets, one colony each."""
    def ext(key, planet, product):
        return cp.ChainOption(key, planet, 4, "P0-P1", product, True, {product: 1000.0}, {}, 100.0, 50.0)

    def fac(key, planet):
        return cp.ChainOption(key, planet, 4, "P1-P2", MECHANICAL_PARTS, False, {MECHANICAL_PARTS: 25.0},
                              {REACTIVE_METALS: 1000.0, PRECIOUS_METALS: 1000.0}, 200.0, 100.0)

    return [ext("rm1", 1, REACTIVE_METALS), ext("pm1", 3, PRECIOUS_METALS),
            ext("rm2", 2, REACTIVE_METALS), ext("pm2", 4, PRECIOUS_METALS),
            fac("fac1", 5), fac("fac2", 6)]


_RATE_PLANETS = [Planet(BARREN, 4000, i, f"P{i}") for i in range(1, 7)]
_RATE_MARKET = Market(sell_value={MECHANICAL_PARTS: 1000.0, REACTIVE_METALS: 5.0, PRECIOUS_METALS: 5.0},
                      buy_cost={REACTIVE_METALS: 10.0, PRECIOUS_METALS: 10.0}, freight_per_unit={})


def test_requested_rate_hits_the_quantity_with_fewer_slots(sd):
    # Six slots run two full chains (50/day). A request of 10/day is one
    # factory at 40% plus the two extractors that feed it.
    chars = [cp.CharacterSpec("a", "A", 6, 4)]
    full = cp.plan_chain(sd, MECHANICAL_PARTS, _RATE_PLANETS, chars, _rate_options(), _RATE_MARKET, allow_buy=False)
    assert full.requested_units_per_day is None
    assert full.used_slots == 6
    assert full.target_units_per_day == pytest.approx(50.0, rel=1e-3)

    capped = cp.plan_chain(sd, MECHANICAL_PARTS, _RATE_PLANETS, chars, _rate_options(), _RATE_MARKET,
                           allow_buy=False, target_per_day=10.0)
    assert capped.status == "optimal"
    assert capped.requested_units_per_day == pytest.approx(10.0)
    assert capped.target_units_per_day == pytest.approx(10.0, rel=1e-3)
    assert capped.max_target_units_per_day == pytest.approx(50.0, rel=1e-3)
    assert capped.used_slots < full.used_slots
    assert any("requested 10" in n for n in capped.notes)
    fac = [a for a in capped.assignments if not a.is_extraction]
    assert len(fac) == 1 and fac[0].run_level == pytest.approx(0.4, rel=1e-2)


def test_requested_rate_above_the_maximum_plans_the_maximum(sd):
    chars = [cp.CharacterSpec("a", "A", 6, 4)]
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, _RATE_PLANETS, chars, _rate_options(), _RATE_MARKET,
                         allow_buy=False, target_per_day=1000.0)
    assert plan.target_units_per_day == pytest.approx(50.0, rel=1e-3)
    assert plan.requested_units_per_day == pytest.approx(1000.0)
    assert plan.used_slots == 6
    assert any("at most" in n for n in plan.notes)


def test_requested_rate_still_caps_when_buying_is_allowed(sd):
    chars = [cp.CharacterSpec("a", "A", 6, 4)]
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, _RATE_PLANETS, chars, _rate_options(), _RATE_MARKET,
                         allow_buy=True, target_per_day=10.0)
    assert plan.target_units_per_day == pytest.approx(10.0, rel=1e-3)
    assert plan.used_slots < 6


def test_greedy_fallback_stops_at_the_requested_rate(sd, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cp, "linprog", broken)
    chars = [cp.CharacterSpec("a", "A", 6, 4)]
    plan = cp.plan_chain(sd, MECHANICAL_PARTS, _RATE_PLANETS, chars, _rate_options(), _RATE_MARKET,
                         allow_buy=False, target_per_day=10.0)
    assert plan.status == "fallback"
    assert plan.target_units_per_day == pytest.approx(25.0, rel=1e-3)   # one whole factory
    assert plan.used_slots < 6
    assert any("greedy" in n for n in plan.notes)


def test_target_per_day_must_be_positive(sd):
    chars = [cp.CharacterSpec("a", "A", 3, 4)]
    with pytest.raises(ValueError, match="target_per_day"):
        cp.plan_chain(sd, MECHANICAL_PARTS, PLANETS3, chars, _hand_options(), MARKET, target_per_day=0)

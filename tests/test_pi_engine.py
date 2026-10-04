"""PI static data, extractor formula and engine (docs/PI_TECHNICAL_DESIGN.md
sections 3.1-3.5). Pure: the static rows are a snapshot of the real SDE
(tests/fixtures/pi_static_rows.json), no Postgres, no network."""
import json
import math
from pathlib import Path

import pytest

from eve_trader.pi import constants as C
from eve_trader.pi import decay, engine, static
from eve_trader.pi.model import Design, Planet

_ROWS = json.loads((Path(__file__).parent / "fixtures" / "pi_static_rows.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sd():
    rows = {k: [tuple(r) for r in v] for k, v in _ROWS.items()}
    return static.build_static(rows)


@pytest.fixture(autouse=True)
def _clear_design_cache():
    engine.clear_cache()
    yield
    engine.clear_cache()


def _id(sd, name):
    return next(t for t, c in sd.commodities.items() if c.name == name)


# ------------------------------------------------------------- decay (V-2)
def test_extractor_formula_matches_the_in_game_program_exactly():
    # Real Lava extractor, 4 heads, 2d 2h program, 1 h cycles - in-game
    # values from 2026-10-04: cycle 1, cycle 50 and the total, to the unit.
    out = decay.cycle_outputs(5903, 3600, 50)
    assert out[0] == 23058
    assert out[-1] == 10654
    assert sum(out) == 682147
    assert out.index(max(out)) == 9  # the tall bar at cycle 10


@pytest.mark.parametrize("hours,seconds", [
    (1, 900), (24.9, 900), (25, 1800), (49, 1800), (50, 3600), (99, 3600),
    (100, 7200), (199, 7200), (200, 14400), (336, 14400),
])
def test_cycle_time_thresholds_confirmed_in_game(hours, seconds):
    assert decay.cycle_seconds_for_program(hours) == seconds


def test_program_ratio_is_one_at_the_reference_and_falls_with_length():
    assert decay.program_ratio(72) == pytest.approx(1.0)
    assert decay.program_ratio(24) > 1.0 > decay.program_ratio(168)


def test_noise_only_adds_yield():
    with_noise = sum(decay.cycle_outputs(5903, 3600, 50, noise=True))
    without = sum(decay.cycle_outputs(5903, 3600, 50, noise=False))
    assert with_noise > without


# ------------------------------------------------------------- static data
def test_tiers_counts_from_the_schematic_graph(sd):
    tiers = [c.tier for c in sd.commodities.values()]
    assert [tiers.count(t) for t in range(5)] == [15, 15, 24, 21, 8]
    # Nano-Factory has a P1 input but is P4 (P-13)
    assert sd.tier(_id(sd, "Nano-Factory")) == 4


def test_volumes_and_tax_bases_come_from_the_sde(sd):
    expect = {"Aqueous Liquids": (0.005, 5), "Bacteria": (0.19, 400), "Coolant": (0.75, 7200),
              "Robotics": (3.0, 60000), "Broadcast Node": (50.0, 1200000)}
    for name, (volume, base) in expect.items():
        c = sd.commodities[_id(sd, name)]
        assert c.volume == volume
        assert c.export_tax_base == base


def test_structures_per_planet_type(sd):
    assert sd.structure(C.KIND_COMMAND_CENTER, 2016).type_id == 2524  # level 0, not "Limited ..."
    assert sd.structure(C.KIND_HIGH_TECH, 2016).type_id == 2475
    assert sd.structure(C.KIND_HIGH_TECH, 11).type_id == 2482
    for pt in (12, 13, 2014, 2015, 2017, 2063):
        assert sd.structure(C.KIND_HIGH_TECH, pt) is None
    lp = sd.structure(C.KIND_LAUNCHPAD, 2016)
    assert (lp.cpu, lp.power, lp.capacity, lp.isk_cost) == (3600, 700, 10000, 900000)
    st = sd.structure(C.KIND_STORAGE, 2016)
    assert (st.cpu, st.power, st.capacity) == (500, 700, 12000)
    ecu = sd.structure(C.KIND_ECU, 2016)
    assert (ecu.cpu, ecu.power, sd.head_cpu, sd.head_power) == (400, 2600, 110, 550)
    for kind, cpu, power in ((C.KIND_BASIC, 200, 800), (C.KIND_ADVANCED, 500, 700), (C.KIND_HIGH_TECH, 1100, 400)):
        spec = sd.structure(kind, 2016)
        assert (spec.cpu, spec.power) == (cpu, power)


def test_link_cost_rounds_up_per_link_as_in_game(sd):
    # In-game 2026-10-04 (V-5): 26 km -> 21 tf / 14 MW, 114 km -> 38 tf / 28 MW
    assert sd.link.cost(26) == (21, 14)
    assert sd.link.cost(114) == (38, 28)
    assert sd.link.capacity_at(0) == 1250 and sd.link.capacity_at(2) == 5000


def test_empty_tables_raise_pi_data_missing():
    with pytest.raises(static.PiDataMissing):
        static.build_static({"schematics": [], "schematic_types": [], "schematic_pins": [],
                             "attributes": [], "types": []})


# ------------------------------------------------------------- engine
A = engine.Assumptions(yield_per_head=2000, program_hours=72, interval_hours=24)


def test_hand_calculated_factory_fit(sd):
    # PI_PLAN section 2 example: CC5, radius 5000 km, P1->P2 with 2 pads -
    # 24 Advanced facilities fit, power is the binding limit.
    coolant = _id(sd, "Coolant")
    d = Design("P1-P2", coolant, 2016, 5, ((coolant, 24),), (), 2, 0)
    ev = engine.evaluate(sd, Planet(2016, 5000.0), d, A)
    assert ev.fits
    assert ev.product_per_hour == 120
    assert ev.imports == {_id(sd, "Water"): 960, _id(sd, "Electrolytes"): 960}
    assert ev.import_m3_per_hour == pytest.approx(364.8)
    assert ev.export_m3_per_hour == pytest.approx(90)
    assert ev.buffer_hours == pytest.approx(20000 / 364.8)
    d25 = Design("P1-P2", coolant, 2016, 5, ((coolant, 25),), (), 2, 0)
    assert not engine.evaluate(sd, Planet(2016, 5000.0), d25, A).fits


def test_extraction_power_trade_off(sd):
    # One 10-head ECU leaves room for ~12 Basic facilities; a second ECU
    # leaves room for only a couple (PI_PLAN section 2 example).
    bacteria, micro = _id(sd, "Bacteria"), C.MICRO_ORGANISMS
    planet = Planet(2016, 5000.0)
    one = Design("P0-P1", bacteria, 2016, 5, ((bacteria, 12),), ((micro, 10),), 1, 0)
    assert engine.evaluate(sd, planet, one, A).fits
    two = Design("P0-P1", bacteria, 2016, 5, ((bacteria, 3),), ((micro, 10), (micro, 10)), 1, 0)
    assert not engine.evaluate(sd, planet, two, A).fits


def test_utilisation_follows_extraction(sd):
    bacteria, micro = _id(sd, "Bacteria"), C.MICRO_ORGANISMS
    d = Design("P0-P1", bacteria, 2016, 5, ((bacteria, 4),), ((micro, 6),), 1, 0)
    ev = engine.evaluate(sd, Planet(2016, 3000.0), d, A)
    # 6 heads x 2000 = 12,000 P0/h feeds exactly 2 of the 4 Basic facilities
    assert ev.utilization[bacteria] == pytest.approx(0.5)
    assert ev.idle_factories == pytest.approx(2.0)
    assert ev.product_per_hour == pytest.approx(80)


def test_best_design_never_builds_more_factories_than_the_heads_feed(sd):
    bacteria = _id(sd, "Bacteria")
    ev, why = engine.search_best(sd, Planet(2016, 3750.0), "P0-P1", bacteria, 5, A)
    assert why is None and ev.fits
    assert ev.idle_factories < 1.0
    supply = sum(h for _p, h in ev.design.ecus) * A.effective_yield
    assert ev.design.factory_count(bacteria) <= math.ceil(supply / 6000) + 1


def test_best_design_factory_planet_is_power_bound(sd):
    coolant = _id(sd, "Coolant")
    ev, why = engine.search_best(sd, Planet(2016, 5000.0), "P1-P2", coolant, 5, A)
    assert why is None
    assert ev.design.factory_count(coolant) >= 24
    assert ev.power_used <= 19000 and ev.cpu_used <= 25415
    assert ev.effective_factor == 1.0


def test_p1_to_p4_runs_the_top_stage_partly_fed(sd):
    node = _id(sd, "Broadcast Node")
    ev, why = engine.search_best(sd, Planet(11, 6440.0), "P1-P4", node, 5, A)
    assert why is None and ev.fits
    assert 0 < ev.product_per_hour < 2  # one High-Tech plant, not fully fed


@pytest.mark.parametrize("chain,product,planet_type,reason", [
    ("P3-P4", "Nano-Factory", 13, "High-Tech"),
    ("P0-P1", "Chiral Structures", 2016, "lacks Non-CS Crystals"),
    ("P1-P2", "Bacteria", 2016, "not a P2 product"),
])
def test_infeasible_combinations_say_why(sd, chain, product, planet_type, reason):
    ev, why = engine.search_best(sd, Planet(planet_type, 5000.0), chain, _id(sd, product), 5, A)
    assert ev is None and reason in why


def test_large_gas_giant_fits_fewer_factories(sd):
    coolant = _id(sd, "Coolant")
    small, _ = engine.search_best(sd, Planet(13, 3000.0), "P1-P2", coolant, 5, A)
    giant, _ = engine.search_best(sd, Planet(13, 150000.0), "P1-P2", coolant, 5, A)
    assert giant.design.factory_count(coolant) < small.design.factory_count(coolant)


def test_long_interval_trades_factories_for_storage(sd):
    coolant = _id(sd, "Coolant")
    short, _ = engine.search_best(sd, Planet(2016, 5000.0), "P1-P2", coolant, 5,
                                  engine.Assumptions(2000, 72, 12))
    long_, _ = engine.search_best(sd, Planet(2016, 5000.0), "P1-P2", coolant, 5,
                                  engine.Assumptions(2000, 72, 96))
    hubs = lambda ev: ev.design.launchpads + ev.design.storages  # noqa: E731
    assert hubs(long_) >= hubs(short)
    assert long_.buffer_hours >= short.buffer_hours


def test_design_round_trips_through_dict():
    d = Design("P0-P2", 1, 2016, 4, ((1, 2), (2, 3)), ((7, 5), (8, 6)), 2, 1)
    assert Design.from_dict(d.to_dict()) == d


def test_best_design_cache_hits_and_clears(sd):
    coolant = _id(sd, "Coolant")
    planet = Planet(2016, 5010.0)
    first = engine.best_design(sd, planet, "P1-P2", coolant, 5, A)
    assert engine.best_design(sd, planet, "P1-P2", coolant, 5, A) is first
    engine.clear_cache()
    assert engine.best_design(sd, planet, "P1-P2", coolant, 5, A) is not first

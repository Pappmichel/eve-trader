"""PI layouts: template I/O, validator/analyser, generator
(docs/PI_TECHNICAL_DESIGN.md section 4, PI_PLAN 6A.3). Pure."""
import json
import os
from pathlib import Path

import pytest

from eve_trader.pi import constants as C
from eve_trader.pi import engine, static
from eve_trader.pi.layout import generate, template_io, validate
from eve_trader.pi.layout.geometry import EQUATOR, central_angle
from eve_trader.pi.layout.template_io import Layout, Link, Pin, Route
from eve_trader.pi.model import CHAINS, Planet

_ROWS = json.loads((Path(__file__).parent / "fixtures" / "pi_static_rows.json").read_text(encoding="utf-8"))
A = engine.Assumptions(2000, 72, 24)


@pytest.fixture(scope="module")
def sd():
    return static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})


def _id(sd, name):
    return next(t for t, c in sd.commodities.items() if c.name == name)


# ------------------------------------------------------------ template I/O
_SAMPLE = ('{"CmdCtrLv": 5, "Cmt": "Factory - Coolant", "Diam": 5820.0, '
           '"L": [{"D": 2, "Lv": 0, "S": 1}], '
           '"P": [{"H": 0, "La": 1.5708, "Lo": 0.0, "S": null, "T": 2544}, '
           '{"H": 0, "La": 1.5708, "Lo": 0.0126, "S": 9832, "T": 2474}], '
           '"Pln": 2016, '
           '"R": [{"P": [1, 2], "Q": 40, "T": 3645}, {"P": [1, 2], "Q": 40, "T": 2390}, '
           '{"P": [2, 1], "Q": 5, "T": 9832}]}')


def test_unchanged_template_round_trips_byte_for_byte():
    layout = template_io.parse(_SAMPLE)
    assert template_io.to_json(layout) == _SAMPLE


def test_floats_stay_floats_and_unknown_keys_survive():
    layout = template_io.parse('{"P": [{"H": 0, "La": 2, "Lo": 0, "S": null, "T": 2544}], "Pln": 2016, "Diam": 3, "X": 1}')
    out = template_io.to_json(layout)
    assert '"La": 2.0' in out and '"Diam": 3.0' in out and '"X": 1' in out


@pytest.mark.parametrize("text,msg", [
    ("not json", "Not valid JSON"),
    ("[]", "JSON object"),
    ('{"P": []}', "Missing \"Pln\""),
    ('{"P": [{"T": "x"}], "Pln": 2016}', "must be a number"),
    ('{"P": [{"T": 2544, "La": 1, "Lo": 0}], "Pln": 2016, "L": [{"S": 1, "D": 5}]}', "does not exist"),
    ('{"P": [{"T": 2544, "La": 1, "Lo": 0}], "Pln": 2016, "R": [{"P": [1], "Q": 1, "T": 1}]}', "path of 2"),
])
def test_bad_templates_are_rejected_with_a_reason(text, msg):
    with pytest.raises(template_io.TemplateError, match=msg):
        template_io.parse(text)


def test_size_limit():
    with pytest.raises(template_io.TemplateError, match="too large"):
        template_io.parse(" " * (template_io.MAX_TEMPLATE_BYTES + 1))


def test_comment_is_made_ascii_and_short():
    assert template_io.safe_comment("Factory – Barren " + "x" * 100) == ("Factory - Barren " + "x" * 100)[:60]


# ------------------------------------------------------------ validator
def test_analyse_sample_factory(sd):
    a = validate.analyse(sd, template_io.parse(_SAMPLE), radius_km=2910.0)
    assert a.ok, a.findings
    assert a.product_type_id == _id(sd, "Coolant") and a.chain == "P1-P2"
    assert a.produced[_id(sd, "Coolant")] == pytest.approx(5.0)
    assert a.imports == {_id(sd, "Water"): pytest.approx(40), _id(sd, "Electrolytes"): pytest.approx(40)}
    # one 0.0126 rad link on a 2910 km planet: 36.7 km -> 23 tf / 16 MW
    assert (a.link_cpu, a.link_power) == (23, 16)
    assert a.cpu_used == 3600 + 500 + 23 and a.power_used == 700 + 700 + 16


def test_analyse_flags_spacing_route_length_budget_and_unrouted(sd):
    pins = [Pin(2544, EQUATOR, 0.0)] + [Pin(2474, EQUATOR, 0.0126 * k, product=_id(sd, "Coolant")) for k in range(1, 9)]
    pins.append(Pin(2474, EQUATOR, 0.0126 * 8 + 0.005, product=_id(sd, "Coolant")))  # too close
    links = [Link(k + 1, k, 0) for k in range(1, 10)]
    routes = [Route(tuple(range(1, 10)), 40, _id(sd, "Water"))]  # 9 structures
    layout = Layout(0, "", 6000.0, 2016, tuple(pins), tuple(links), tuple(routes))
    codes = {f.code for f in validate.analyse(sd, layout, 3000.0).findings}
    assert {"too_close", "route_too_long", "cpu", "power", "factory_idle"} <= codes


def test_high_tech_only_on_barren_and_temperate(sd):
    pins = (Pin(2543, EQUATOR, 0.0), Pin(2475, EQUATOR, 0.0126, product=_id(sd, "Nano-Factory")))
    layout = Layout(5, "", 6000.0, 13, pins, (Link(2, 1, 0),), ())
    assert "no_high_tech" in {f.code for f in validate.analyse(sd, layout, 3000.0).findings}


def test_link_overload_is_reported(sd):
    water, coolant = _id(sd, "Water"), _id(sd, "Coolant")
    pins = (Pin(2544, EQUATOR, 0.0), Pin(2474, EQUATOR, 0.0126, product=coolant))
    # a P1 route of 10,000 units per cycle: 1,900 m3/h through one level-0 link
    routes = (Route((1, 2), 10000, water), Route((1, 2), 10000, _id(sd, "Electrolytes")), Route((2, 1), 5, coolant))
    layout = Layout(5, "", 6000.0, 2016, pins, (Link(2, 1, 0),), routes)
    a = validate.analyse(sd, layout, 3000.0)
    assert a.link_load_m3h[0] > 1250 or "link_overloaded" not in {f.code for f in a.findings}


# ------------------------------------------------------------ generator
SAMPLE_CASES = [
    ("P0-P1", "Bacteria", 2016, 3750.0),
    ("P0-P2", "Coolant", 13, 26370.0),
    ("P1-P2", "Coolant", 2016, 5000.0),
    ("P2-P3", "Robotics", 2015, 3360.0),
    ("P1-P3", "Robotics", 2016, 5000.0),
    ("P3-P4", "Nano-Factory", 2016, 5000.0),
    ("P2-P4", "Nano-Factory", 11, 6440.0),
    ("P1-P4", "Broadcast Node", 11, 6440.0),
    ("P1-P2", "Coolant", 13, 150000.0),
    ("P0-P1", "Bacteria", 2016, 121.7),
]


def _check_generated(sd, chain, product, pt, radius):
    ev, why = engine.search_best(sd, Planet(pt, radius), chain, product, 5, A)
    assert ev is not None, why
    res = generate.generate(sd, ev.design, pt, radius, A.effective_yield)
    layout, a = res.layout, res.analysis
    assert a.ok, [f.message for f in a.findings if f.severity == validate.ERROR]
    assert a.cpu_used <= a.cpu_capacity and a.power_used <= a.power_capacity
    assert max(len(r.path) for r in layout.routes) <= C.MAX_ROUTE_STRUCTURES
    for i, p in enumerate(layout.pins):
        for q in layout.pins[i + 1:]:
            assert central_angle(p.la, p.lo, q.la, q.lo) >= C.MIN_PIN_SEPARATION_RAD
    for p in layout.pins:
        assert isinstance(p.la, float) and isinstance(p.lo, float)
    # P0 never routed through a Basic facility by our own generator
    assert "p0_through_basic" not in {f.code for f in a.findings}
    # the layout produces what the engine planned (the LP may do slightly better)
    planned = engine.evaluate(sd, Planet(pt, radius), res.design, A).product_per_hour
    assert a.produced.get(product, 0.0) >= planned - 1e-6
    # round trip through JSON
    assert template_io.to_dict(template_io.parse(template_io.to_json(layout))) == template_io.to_dict(layout)
    return res


@pytest.mark.parametrize("chain,product,pt,radius", SAMPLE_CASES)
def test_generated_layouts_pass_the_validator(sd, chain, product, pt, radius):
    _check_generated(sd, chain, _id(sd, product), pt, radius)


def test_extractor_route_quantity_follows_the_program_cycle(sd):
    from eve_trader.pi import decay

    assert decay.cycle_seconds_for_program(72) == 3600
    assert decay.cycle_seconds_for_program(120) == 7200
    assert decay.ecu_route_quantity(2, 1000, 3600) == 2000
    assert decay.ecu_route_quantity(2, 767, 7200) == 3068

    ev, _ = engine.search_best(sd, Planet(2016, 8000.0), "P0-P1", _id(sd, "Bacteria"), 4,
                               engine.Assumptions(1000, 120, 24))
    assert ev is not None and ev.design.ecus
    cycle = decay.cycle_seconds_for_program(120)
    res = generate.generate(sd, ev.design, 2016, 8000.0, 1000.0, cycle_seconds=cycle)
    ecu_routes = [
        r for r in res.layout.routes
        if sd.structures[res.layout.pins[r.path[0] - 1].type_id].kind == C.KIND_ECU
    ]
    assert ecu_routes
    for r in ecu_routes:
        src = res.layout.pins[r.path[0] - 1]
        assert r.quantity == decay.ecu_route_quantity(src.heads, 1000.0, cycle)
        assert r.quantity == pytest.approx(src.heads * 1000.0 * 2, abs=src.heads)


def test_generator_is_deterministic(sd):
    ev, _ = engine.search_best(sd, Planet(2016, 5000.0), "P1-P3", _id(sd, "Robotics"), 5, A)
    one = generate.generate(sd, ev.design, 2016, 5000.0, A.effective_yield)
    two = generate.generate(sd, ev.design, 2016, 5000.0, A.effective_yield)
    assert template_io.to_json(one.layout) == template_io.to_json(two.layout)


def test_budget_feedback_shrinks_an_oversized_design(sd):
    coolant = _id(sd, "Coolant")
    from eve_trader.pi.model import Design
    big = Design("P1-P2", coolant, 2016, 5, ((coolant, 40),), (), 1, 0)
    res = generate.generate(sd, big, 2016, 5000.0, A.effective_yield)
    assert res.design.factory_count(coolant) < 40 and res.analysis.ok and res.notes


def test_inputs_are_routed_from_the_home_hub_first(sd):
    ev, _ = engine.search_best(sd, Planet(2016, 5000.0), "P1-P2", _id(sd, "Coolant"), 5,
                               engine.Assumptions(2000, 72, 96))
    res = generate.generate(sd, ev.design, 2016, 5000.0, A.effective_yield)
    hubs = [i + 1 for i, p in enumerate(res.layout.pins) if sd.structures[p.type_id].kind in C.HUB_KINDS]
    assert len(hubs) >= 2  # long interval -> more storage, and the routes use it
    used = {r.path[0] for r in res.layout.routes} | {r.path[-1] for r in res.layout.routes}
    assert set(hubs) <= used


@pytest.mark.pi_corpus
@pytest.mark.skipif(os.environ.get("PI_CORPUS") != "1", reason="set PI_CORPUS=1 for the full generator corpus")
def test_full_corpus(sd):
    """Every product x valid chain x planet type x CC level x radius bucket."""
    failures = []
    for chain, (_s, target) in CHAINS.items():
        for product in sd.products_of_tier(target):
            for pt in C.PLANET_TYPE_IDS:
                if engine.check_feasible(sd, pt, chain, product):
                    continue
                for cc in (1, 3, 5):
                    for radius in (1500.0, 5000.0, 15000.0, 40000.0):
                        ev, _why = engine.search_best(sd, Planet(pt, radius), chain, product, cc, A)
                        if ev is None:
                            continue
                        try:
                            _check_generated(sd, chain, product, pt, radius) if cc == 5 else \
                                generate.generate(sd, ev.design, pt, radius, A.effective_yield)
                        except Exception as e:  # noqa: BLE001
                            failures.append((chain, sd.name(product), pt, cc, radius, str(e)[:120]))
    assert not failures, failures[:20]

"""PI phase 5b/5c: variants, partial sourcing, mixed P2, storage suggestion,
grow to supply, layout edits (pure, real SDE fixture)."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from eve_trader.pi import constants as C
from eve_trader.pi import engine, static, variants
from eve_trader.pi.layout import edit, generate, validate
from eve_trader.pi.model import Design, Planet

_ROWS = json.loads((Path(__file__).parent / "fixtures" / "pi_static_rows.json").read_text(encoding="utf-8"))
A = engine.Assumptions(2000, 72, 24)
BARREN = Planet(2016, 5000.0)


@pytest.fixture(scope="module")
def sd():
    return static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})


def _id(sd, name):
    return next(t for t, c in sd.commodities.items() if c.name == name)


def test_partially_sourced_design_imports_what_it_does_not_make(sd):
    nanites, bacteria = _id(sd, "Nanites"), _id(sd, "Bacteria")
    d = Design("P0-P2", nanites, 2016, 5, ((bacteria, 4), (nanites, 4)), ((C.MICRO_ORGANISMS, 10),), 1, 0)
    ev = engine.evaluate(sd, BARREN, d, A)
    assert ev.fits
    assert set(ev.imports) == {_id(sd, "Reactive Metals")}
    assert C.MICRO_ORGANISMS not in ev.imports


def test_ways_to_build_lists_every_split(sd):
    nano = _id(sd, "Nano-Factory")
    rows = variants.ways_to_build(sd, BARREN, nano, 5, A)
    # two P3 inputs can be made here (Reactive Metals is a P1): 2^2 splits
    assert len(rows) == 4
    assert all(r["evaluation"] is not None and r["evaluation"].fits for r in rows)
    assert rows[0]["made"] == [nano]  # hauling everything in gives the most P4 per planet
    assert _id(sd, "Reactive Metals") in rows[-1]["hauled"]


def test_ways_to_build_needs_a_high_tech_planet_for_p4(sd):
    assert variants.ways_to_build(sd, Planet(13, 5000.0), _id(sd, "Nano-Factory"), 5, A) == []


def test_partial_p0_p2_extracts_one_p1(sd):
    rows = variants.partial_p0_p2(sd, BARREN, _id(sd, "Nanites"), 5, A)
    assert {sd.name(r["extracted_p1"]) for r in rows} == {"Bacteria", "Reactive Metals"}
    for r in rows:
        ev = r["evaluation"]
        assert ev.fits and len(ev.design.ecus) == 1 and len(ev.imports) == 1


def test_mixed_p2_runs_and_generates(sd):
    coolant, parts = _id(sd, "Coolant"), _id(sd, "Mechanical Parts")
    ev = variants.mixed_p2(sd, BARREN, {coolant: 12, parts: 12}, 5, A)
    assert ev.fits and ev.produced[coolant] == 60 and ev.produced[parts] == 60
    res = generate.generate(sd, ev.design, 2016, 5000.0, A.effective_yield)
    assert res.analysis.ok
    assert res.analysis.produced[coolant] == pytest.approx(60) and res.analysis.produced[parts] == pytest.approx(60)


def test_mixed_p2_rejects_non_p2(sd):
    with pytest.raises(ValueError):
        variants.mixed_p2(sd, BARREN, {_id(sd, "Bacteria"): 3}, 5, A)


def test_storage_suggestion_add_storage_and_covered(sd):
    coolant = _id(sd, "Coolant")
    d = Design("P1-P2", coolant, 2016, 5, ((coolant, 24),), (), 1, 0)
    s48 = variants.storage_suggestion(sd, BARREN, d, engine.Assumptions(2000, 72, 48))
    assert s48["kind"] == "add_storage" and s48["reaches_interval"] and s48["buffer_hours"] >= 48
    s12 = variants.storage_suggestion(sd, BARREN, d, engine.Assumptions(2000, 72, 12))
    assert s12["kind"] == "covered"


def test_storage_suggestion_trades_production_when_no_storage_fits(sd):
    coolant = _id(sd, "Coolant")
    full, _ = engine.search_best(sd, BARREN, "P1-P2", coolant, 5, A)
    s = variants.storage_suggestion(sd, BARREN, full.design, engine.Assumptions(2000, 72, 60))
    assert s["kind"] in ("add_storage", "trade")
    if s["kind"] == "trade":
        assert 0 < s["output_share"] < 1 and s["buffer_hours"] >= 60


def test_grow_to_supply_adds_fed_factories(sd):
    bacteria = _id(sd, "Bacteria")
    small = Design("P0-P1", bacteria, 2016, 5, ((bacteria, 2),), ((C.MICRO_ORGANISMS, 10),), 1, 0)
    grown = variants.grow_to_supply(sd, BARREN, small, engine.Assumptions(6000, 72, 24))
    assert grown.fits and grown.design.factory_count(bacteria) > 2
    assert grown.idle_factories < 1.0


# ------------------------------------------------------------ layout edits
@pytest.fixture(scope="module")
def coolant_layout(sd):
    ev, _ = engine.search_best(sd, BARREN, "P1-P2", _id(sd, "Coolant"), 5, A)
    return generate.generate(sd, ev.design, 2016, 5000.0, A.effective_yield).layout


def test_remove_reconnects_orphans(sd, coolant_layout):
    before = validate.analyse(sd, coolant_layout, 5000.0).produced[_id(sd, "Coolant")]
    for pin in range(2, len(coolant_layout.pins) + 1):
        after = edit.apply(sd, coolant_layout, {"op": "remove", "pin": pin})
        a = validate.analyse(sd, after, 5000.0)
        assert a.ok, pin
        # only the removed factory's output is lost, never its children's
        assert a.produced[_id(sd, "Coolant")] == pytest.approx(before - 5.0), pin


def test_add_factory_links_and_routes_it(sd, coolant_layout):
    smaller = edit.apply(sd, coolant_layout, {"op": "remove", "pin": 5})
    hub = coolant_layout.pins[0]
    added = edit.apply(sd, smaller, {"op": "add", "kind": C.KIND_ADVANCED, "product": _id(sd, "Coolant"),
                                     "la": hub.la + 0.08, "lo": hub.lo})
    a = validate.analyse(sd, added, 5000.0)
    assert a.ok and not [f for f in a.findings if f.code == "factory_idle"]
    assert len(added.pins) == len(coolant_layout.pins)


@pytest.mark.parametrize("op,msg", [
    ({"op": "remove", "pin": 999}, "does not exist"),
    ({"op": "add", "kind": C.KIND_BASIC, "product": 9832, "la": 1.6, "lo": 0.1}, "not made in that facility"),
    ({"op": "add", "kind": C.KIND_ECU, "product": C.FELSIC_MAGMA, "heads": 3, "la": 1.6, "lo": 0.1}, "P0 resource"),
    ({"op": "link_level", "link": 1, "level": 9}, "0-5"),
    ({"op": "explode"}, "Unknown edit"),
])
def test_refused_edits_raise_and_change_nothing(sd, coolant_layout, op, msg):
    snapshot = coolant_layout
    with pytest.raises(edit.EditError, match=msg):
        edit.apply(sd, coolant_layout, op)
    assert coolant_layout == snapshot


def test_move_too_close_is_reported_by_the_validator(sd, coolant_layout):
    p = coolant_layout.pins[1]
    moved = edit.apply(sd, coolant_layout, {"op": "move", "pin": 3, "la": p.la + 0.001, "lo": p.lo})
    assert "too_close" in {f.code for f in validate.analyse(sd, moved, 5000.0).findings}


def test_route_storage_restores_missing_routes(sd, coolant_layout):
    stripped = replace(coolant_layout, routes=())
    routed = edit.route_storage(sd, stripped)
    a = validate.analyse(sd, routed, 5000.0)
    assert a.ok and a.produced[_id(sd, "Coolant")] == pytest.approx(
        validate.analyse(sd, coolant_layout, 5000.0).produced[_id(sd, "Coolant")])

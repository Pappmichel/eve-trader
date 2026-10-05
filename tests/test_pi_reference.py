"""Reference layouts (pi/layout/reference.py): community templates adapted to
another product and planet, trimmed and grown. Pure: static data from the SDE
snapshot fixture."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from eve_trader.pi import constants as C
from eve_trader.pi import engine, static
from eve_trader.pi.model import Planet
from eve_trader.pi.layout import generate, reference, template_io, validate

_ROWS = json.loads((Path(__file__).parent / "fixtures" / "pi_static_rows.json").read_text(encoding="utf-8"))
_A = engine.Assumptions()
_Y = _A.effective_yield


@pytest.fixture(scope="module")
def sd():
    return static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})


def _id(sd, name):
    return next(t for t, c in sd.commodities.items() if c.name == name)


def _design(sd, chain, product, pt=2016, radius=5000.0, a=_A):
    ev, why = engine.best_design(sd, Planet(pt, radius), chain, product, 5, a, exact_radius=True)
    assert ev is not None, why
    return ev.design


def test_every_reference_parses_and_passes_the_validator(sd):
    refs = reference.load()
    assert len(refs) > 300
    for r in refs:
        a = validate.analyse(sd, r.layout)
        assert a.ok, (r.chain, [f.message for f in a.findings if f.severity == validate.ERROR])
        assert a.chain == r.chain
        assert r.layout.comment == ""            # no comments (or names) from the source site


def test_map_products_pairs_trees_by_shape(sd):
    coolant, oxides = _id(sd, "Coolant"), _id(sd, "Oxides")
    made = {coolant}
    m = reference.map_products(sd, coolant, made, set(), oxides, {oxides}, set())
    assert m[coolant] == oxides
    in_old = {t for t, _q in sd.schematic_by_output[coolant].inputs}
    in_new = {t for t, _q in sd.schematic_by_output[oxides].inputs}
    assert {m[t] for t in in_old} == in_new
    # a made input cannot pair with an imported one
    p1 = next(iter(in_old))
    assert reference.map_products(sd, coolant, {coolant, p1}, set(), oxides, {oxides}, set()) is None


def test_adapt_rewrites_products_quantities_and_planet(sd):
    oxides = _id(sd, "Oxides")
    d = _design(sd, "P1-P2", oxides, pt=2015)
    ref = next(r for r in reference.load() if r.chain == "P1-P2" and r.layout.planet_type_id != 2015)
    lay = reference.adapt(sd, ref, d, 2015, 4000.0, _Y)
    assert lay is not None
    assert lay.planet_type_id == 2015 and lay.diameter_km == 8000.0 and lay.cc_level == d.cc_level
    assert len(lay.pins) == len(ref.layout.pins) and lay.links == ref.layout.links
    assert all(sd.structures[p.type_id].planet_type_id == 2015 for p in lay.pins)
    inputs = dict(sd.schematic_by_output[oxides].inputs)
    for r in lay.routes:
        dst = lay.pins[r.path[-1] - 1]
        if sd.structures[dst.type_id].kind in C.FACTORY_KINDS and r.commodity in inputs:
            assert r.quantity == inputs[r.commodity]
    assert {p.product for p in lay.pins if p.product} <= {oxides, *inputs}


def test_adapt_refuses_a_chain_or_level_that_does_not_fit(sd):
    d = _design(sd, "P1-P2", _id(sd, "Coolant"))
    p0p1 = next(r for r in reference.load() if r.chain == "P0-P1")
    assert reference.adapt(sd, p0p1, d, 2016, 5000.0, _Y) is None
    lvl5 = next(r for r in reference.load() if r.chain == "P1-P2" and r.cc_level == 5)
    assert reference.adapt(sd, lvl5, replace(d, cc_level=4), 2016, 5000.0, _Y) is None


@pytest.mark.parametrize("chain,product,pt", [
    ("P0-P1", "Bacteria", 2016),
    ("P0-P2", None, 2016),
    ("P1-P2", "Coolant", 2016),
    ("P2-P3", None, 11),
    ("P1-P4", None, 2016),
])
def test_from_references_gives_a_valid_importable_layout(sd, chain, product, pt):
    tier = int(chain[-1])
    if product is None:
        product_id = next(t for t in sorted(sd.commodities) if (sd.tier(t) or 0) == tier
                          and not engine.check_feasible(sd, pt, chain, t))
    else:
        product_id = _id(sd, product)
    d = _design(sd, chain, product_id, pt)
    res = reference.from_references(sd, d, pt, 5000.0, _Y, _A.interval_hours)
    assert res is not None
    assert res.analysis.ok and res.effective_output > 0
    assert res.layout.planet_type_id == pt
    # the template the user pastes into the game round-trips unchanged
    text = template_io.to_json(res.layout)
    assert template_io.to_json(template_io.parse(text)) == text
    # colony's own products and P0 are never hauled in
    assert not set(res.analysis.imports) & reference.self_supplied(sd, d)
    assert res.design.product_type_id == product_id and res.design.chain == chain


def test_validator_bars_imports_of_self_supplied_commodities(sd):
    bacteria = _id(sd, "Bacteria")
    d = _design(sd, "P0-P1", bacteria)
    ref = next(r for r in reference.load() if r.chain == "P0-P1"
               and reference.adapt(sd, r, d, 2016, 5000.0, _Y) is not None)
    lay = reference.adapt(sd, ref, d, 2016, 5000.0, _Y)
    free = validate.analyse(sd, lay, 5000.0, _Y)
    barred = validate.analyse(sd, lay, 5000.0, _Y, reference.self_supplied(sd, d))
    assert not set(barred.imports) & reference.self_supplied(sd, d)
    assert barred.exports.get(bacteria, 0) <= free.exports.get(bacteria, 0) + 1e-9


def test_grow_never_lowers_output_and_keeps_the_reference_structures(sd):
    coolant = _id(sd, "Coolant")
    d = _design(sd, "P1-P2", coolant)
    local = reference.self_supplied(sd, d)
    ref = next(r for r in reference.load() if r.chain == "P1-P2"
               and reference.adapt(sd, r, d, 2016, 5000.0, _Y) is not None)
    lay, an = reference.settle(sd, reference.adapt(sd, ref, d, 2016, 5000.0, _Y), 5000.0, _Y, local)
    before = reference.effective_output(an, coolant, _A.interval_hours)
    grown, gan = reference.grow(sd, lay, an, coolant, 5000.0, _Y, _A.interval_hours, local)
    assert gan.ok
    assert reference.effective_output(gan, coolant, _A.interval_hours) >= before
    assert grown.pins[:len(lay.pins)] == lay.pins          # nothing that was there moves
    assert len(grown.pins) >= len(lay.pins)


def test_prune_drops_unfed_factories_without_losing_output(sd):
    bacteria = _id(sd, "Bacteria")
    d = _design(sd, "P0-P1", bacteria)
    local = reference.self_supplied(sd, d)
    for ref in reference.load():
        lay = reference.adapt(sd, ref, d, 2016, 5000.0, _Y)
        if lay is None:
            continue
        lay, an = reference.settle(sd, lay, 5000.0, _Y, local)
        if not an.ok:
            continue
        before = reference.effective_output(an, bacteria, _A.interval_hours)
        pruned, pan = reference.prune(sd, lay, an, bacteria, 5000.0, _Y, _A.interval_hours, local)
        assert pan.ok and reference.effective_output(pan, bacteria, _A.interval_hours) >= before - 1e-9
        assert len(pruned.pins) <= len(lay.pins)


def test_validator_accepts_in_game_rounding_of_the_spacing(sd):
    """In-game exports sit at 0.01199x rad; well below 0.012 is still refused."""
    gen = generate.generate(sd, _design(sd, "P1-P2", _id(sd, "Coolant")), 2016, 5000.0, _Y).layout
    a, b = gen.pins[0], gen.pins[1]

    def at(angle):
        pins = list(gen.pins)
        # place pin 2 `angle` rad east of pin 1 on its parallel
        from eve_trader.pi.layout.geometry import offset

        la, lo = offset(a.la, a.lo, 0.0, angle)
        pins[1] = replace(b, la=a.la, lo=lo)
        return replace(gen, pins=tuple(pins))

    def close(layout):
        return [f for f in validate.analyse(sd, layout, 5000.0, _Y).findings
                if f.code == "too_close" and f.pins[:1] == (1,) and 2 in f.pins]

    assert not close(at(0.011993))
    assert close(at(0.0115))


def test_rebalance_splits_factories_in_the_design_proportions(sd):
    robotics = next(t for t, c in sd.commodities.items() if c.name == "Robotics")
    d = _design(sd, "P1-P3", robotics)
    want = dict(d.factories)
    for ref in reference.load():
        lay = reference.adapt(sd, ref, d, 2016, 5000.0, _Y)
        if lay is None:
            continue
        balanced = reference.rebalance(sd, lay, d)
        assert [(p.la, p.lo, p.type_id) for p in balanced.pins] == [(p.la, p.lo, p.type_id) for p in lay.pins]
        got = reference.design_of(sd, balanced, "P1-P3", robotics)
        total = got.total_factories
        for t, n in got.factories:
            assert abs(n - total * want[t] / d.total_factories) <= 1.0 + 1e-9
        break
    else:
        pytest.fail("no P1-P3 reference maps onto Robotics")


def test_shares_largest_remainder():
    # 9.6 / 9.6 / 4.8 -> floors 9/9/4, the two largest remainders get one more
    assert reference._shares(24, {1: 10, 2: 10, 3: 5}) == {1: 10, 2: 9, 3: 5}
    assert reference._shares(3, {1: 100, 2: 1, 3: 1}) == {1: 1, 2: 1, 3: 1}
    assert reference._shares(0, {1: 1}) == {1: 0}


def test_prune_drops_hubs_a_short_interval_does_not_need(sd):
    """A reference built for weekly visits carries extra launchpads; at a
    one-day interval their budget goes into factories instead."""
    nano = _id(sd, "Nano-Factory")
    d = _design(sd, "P2-P4", nano)
    res = reference.from_references(sd, d, 2016, 5000.0, _Y, _A.interval_hours)

    def hubs(layout):
        return sum(sd.structures[p.type_id].kind in C.HUB_KINDS for p in layout.pins)

    assert res is not None and res.analysis.ok
    assert hubs(res.layout) < hubs(res.reference.layout)

"""PI actions for layouts, templates, system analysis, production demand,
skills and demand costing. Pure: static data from the SDE snapshot fixture,
storage readers and prices monkeypatched."""
import json
from pathlib import Path

import pytest

from eve_trader.actions import ActionError
from eve_trader.pi import actions as pa
from eve_trader.pi import constants as C
from eve_trader.pi import demand, engine, skills, static
from eve_trader.pi import economics as econ
from eve_trader.pi.config import PiConfig
from eve_trader.pi.layout import template_io

_ROWS = json.loads((Path(__file__).parent / "fixtures" / "pi_static_rows.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sd():
    return static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})


def _fake_prices(sd):
    sell = {t: 100.0 * (10 ** c.tier) / 10 for t, c in sd.commodities.items()}
    return econ.Prices(sell=sell, buy={t: v * 0.9 for t, v in sell.items()})


@pytest.fixture(autouse=True)
def _patched(monkeypatch, sd):
    engine.clear_cache()
    monkeypatch.setattr(pa, "_static", lambda: sd)
    monkeypatch.setattr(pa, "_medians_cache", {pt: 5000.0 for pt in C.PLANET_TYPE_IDS})
    monkeypatch.setattr(pa, "_prices", lambda s, cfg, with_history=True: _fake_prices(s))
    yield
    engine.clear_cache()


def _id(sd, name):
    return next(t for t, c in sd.commodities.items() if c.name == name)


def _cfg(**over):
    return PiConfig(pi_min_isk_per_planet_day=0.0, pi_market_share_warning=1.0, **over)


def _gen(sd, **kw):
    args = dict(chain="P1-P2", product_type_id=_id(sd, "Coolant"), planet_type_id=2016, radius_km=5000.0,
                cc_level=5, cfg=_cfg())
    args.update(kw)
    return pa.do_generate_layout(**args)


# ------------------------------------------------------------ generate
def test_generate_free_planet(sd):
    r = _gen(sd)
    layout = template_io.parse(r["template_json"])
    assert layout.planet_type_id == 2016
    assert r["analysis"]["ok"] is True
    assert r["planet"]["planet_type_id"] == 2016 and r["planet"]["radius_km"] == 5000.0
    assert r["analysis"]["product_name"] == "Coolant"


def test_generate_star_shape(sd):
    r = _gen(sd, shape="star")
    template_io.parse(r["template_json"])
    assert r["analysis"]["ok"] is True
    assert r["shape"] in ("star", "standard")


def test_generate_spiral_on_full_colony_falls_back_with_note(sd):
    r = _gen(sd, shape="spiral")
    assert r["shape"] == "standard"
    assert any("spiral" in n for n in r["notes"])
    assert r["analysis"]["ok"] is True


def test_generate_unknown_shape(sd):
    with pytest.raises(ActionError, match="Unknown shape"):
        _gen(sd, shape="blob")


def test_generate_infeasible(sd):
    with pytest.raises(ActionError):
        _gen(sd, product_type_id=_id(sd, "Water"))  # a P1 product on a P1-P2 chain
    with pytest.raises(ActionError, match="Unknown chain"):
        _gen(sd, chain="P9-P9")
    with pytest.raises(ActionError):
        _gen(sd, chain="P1-P4", product_type_id=_id(sd, "Coolant"))


def test_generate_with_known_planet(sd, monkeypatch):
    monkeypatch.setattr(pa.storage, "get_pi_planet", lambda pid: (pid, "P I", 30000142, 2016, 5000.0))
    monkeypatch.setattr(pa.storage, "get_solar_system", lambda sid: (sid, "Jita", 0.9, 10000002))
    r = _gen(sd, planet_id=77, planet_type_id=None, radius_km=None)
    assert r["planet"]["planet_id"] == 77
    monkeypatch.setattr(pa.storage, "get_pi_planet", lambda pid: None)
    with pytest.raises(ActionError, match="Unknown PI planet"):
        _gen(sd, planet_id=78, planet_type_id=None, radius_km=None)


# ------------------------------------------------------------ validate
def test_validate_generated_template(sd):
    g = _gen(sd)
    v = pa.do_validate_layout(g["template_json"], radius_km=5000.0, cfg=_cfg())
    assert v["analysis"]["ok"] is True
    assert v["template"] == g["template"]
    # the dict form is accepted too
    assert pa.do_validate_layout(g["template"], radius_km=5000.0, cfg=_cfg())["analysis"]["ok"] is True


def test_validate_garbage_raises_template_message():
    with pytest.raises(ActionError, match="Not valid JSON"):
        pa.do_validate_layout("this is not json", cfg=_cfg())


# ------------------------------------------------------------ retarget
def test_retarget_planet_type_rewrites_structure_ids(sd):
    g = _gen(sd)
    old = template_io.parse(g["template_json"])
    r = pa.do_retarget_template(g["template_json"], planet_type_id=13, radius_km=5000.0, cfg=_cfg())
    new = template_io.parse(r["template_json"])
    assert new.planet_type_id == 13 and len(new.pins) == len(old.pins)
    for o, n in zip(old.pins, new.pins):
        assert sd.structures[n.type_id].kind == sd.structures[o.type_id].kind
        assert n.type_id == sd.structure(sd.structures[o.type_id].kind, 13).type_id
    assert len(new.routes) == len(old.routes)
    assert r["analysis"] is not None


def test_retarget_product_swap_same_tier(sd):
    g = _gen(sd)
    old = template_io.parse(g["template_json"])
    mech = _id(sd, "Mechanical Parts")
    r = pa.do_retarget_template(g["template_json"], product_type_id=mech, radius_km=5000.0, cfg=_cfg())
    new = template_io.parse(r["template_json"])
    assert len(new.routes) == len(old.routes)
    assert {x.commodity for x in new.routes} != {x.commodity for x in old.routes}
    assert mech in {x.commodity for x in new.routes}
    assert r["analysis"]["product_type_id"] == mech


def test_retarget_other_tier_rejected(sd):
    g = _gen(sd)
    with pytest.raises(ActionError, match="same tier"):
        pa.do_retarget_template(g["template_json"], product_type_id=_id(sd, "Water"), cfg=_cfg())


def test_retarget_p4_to_gas_rejected(sd):
    p4 = next(t for t, c in sd.commodities.items() if c.tier == 4)
    chain = next(ch for ch, (_s, t) in pa.CHAINS.items() if t == 4)
    g = pa.do_generate_layout(chain, p4, planet_type_id=2016, radius_km=5000.0, cc_level=5, cfg=_cfg())
    with pytest.raises(ActionError):
        pa.do_retarget_template(g["template_json"], planet_type_id=13, cfg=_cfg())


def test_retarget_garbage_and_unknown_planet_type(sd):
    with pytest.raises(ActionError):
        pa.do_retarget_template("nope", planet_type_id=13, cfg=_cfg())
    g = _gen(sd)
    with pytest.raises(ActionError, match="Unknown planet type"):
        pa.do_retarget_template(g["template_json"], planet_type_id=1, cfg=_cfg())


# ------------------------------------------------------------ shapes
def test_shape_providers_return_core_first_and_unique_cells():
    from eve_trader.pi.layout import shapes
    from eve_trader.pi.layout.generate import Cell

    for name in shapes.SHAPES:
        cells = shapes.provider(name)(12)
        assert len(cells) == 12 and len(set(cells)) == 12 and cells[0] == Cell(0, 0)
    with pytest.raises(KeyError):
        shapes.provider("nope")
    from eve_trader.pi.layout.generate import GenerateError

    with pytest.raises(GenerateError):
        shapes.provider("star")(100000)


# ------------------------------------------------------------ system analysis
def _fake_system(monkeypatch, planets):
    monkeypatch.setattr(pa.storage, "get_solar_system", lambda sid: (sid, "Test", 0.9, 10000002))
    monkeypatch.setattr(pa.storage, "pi_planets_in_system", lambda sid: planets)


def test_system_analysis_plan(monkeypatch):
    _fake_system(monkeypatch, [(1, "Test I", 2016, 3000.0), (2, "Test II", 2015, 4000.0), (3, "Test III", 13, 6000.0)])
    r = pa.do_system_analysis(1, slots=4, characters=2, cc_level=5, cfg=_cfg())
    assert r["plan"]["status"] in ("optimal", "time_limit", "fallback")
    assert r["plan"]["used_slots"] <= 4
    assert len(r["planets"]) == 3 and all("best" in p for p in r["planets"])
    assert r["cannot"] == []
    assert r["slots"] == 4 and r["characters"] == 2


def test_system_analysis_without_barren_or_temperate(monkeypatch):
    _fake_system(monkeypatch, [(2, "Test II", 2015, 4000.0), (3, "Test III", 13, 6000.0)])
    r = pa.do_system_analysis(1, slots=4, characters=2, cc_level=5, cfg=_cfg())
    assert any("P4" in c for c in r["cannot"])
    assert r["plan"]["used_slots"] <= 4


def test_system_analysis_errors(monkeypatch):
    _fake_system(monkeypatch, [])
    with pytest.raises(ActionError, match="no PI planets"):
        pa.do_system_analysis(1, slots=4, characters=2, cc_level=5, cfg=_cfg())
    _fake_system(monkeypatch, [(1, "x", 2016, 3000.0)])
    with pytest.raises(ActionError, match="Slots"):
        pa.do_system_analysis(1, slots=0, characters=2, cc_level=5, cfg=_cfg())
    monkeypatch.setattr(pa.storage, "get_solar_system", lambda sid: None)
    with pytest.raises(ActionError, match="Unknown solar system"):
        pa.do_system_analysis(1, slots=1, characters=1, cc_level=5, cfg=_cfg())


def test_system_analysis_uses_characters_overview_when_unset(monkeypatch):
    _fake_system(monkeypatch, [(1, "x", 2016, 3000.0)])
    monkeypatch.setattr(pa, "do_characters", lambda cfg=None: {"total_slots": 3, "character_count": 1, "max_cc_level": 4})
    r = pa.do_system_analysis(1, cfg=_cfg())
    assert (r["slots"], r["characters"], r["cc_level"]) == (3, 1, 4)

    def boom(cfg=None):
        raise RuntimeError("no token store")

    monkeypatch.setattr(pa, "do_characters", boom)
    cfg = _cfg(pi_characters=2, pi_planets_per_character=5)
    r = pa.do_system_analysis(1, cfg=cfg)
    assert (r["slots"], r["characters"]) == (10, 2)


# ------------------------------------------------------------ production demand
def test_production_demand_rows(sd, monkeypatch):
    p1, p2 = _id(sd, "Water"), _id(sd, "Coolant")
    monkeypatch.setattr(pa.storage, "load_latest_buy_list", lambda: {p1: 1000.0, p2: 500.0, 34: 99999.0})
    r = pa.do_production_demand(_cfg())
    assert {row["type_id"] for row in r["rows"]} == {p1, p2}
    for row in r["rows"]:
        assert row["pi_unit_cost"] is not None and row["chain"] in pa.CHAINS
        assert isinstance(row["make_via_pi"], bool)
    assert r["days"] == _cfg().pi_demand_days


def test_production_demand_empty(monkeypatch):
    monkeypatch.setattr(pa.storage, "load_latest_buy_list", lambda: {})
    assert pa.do_production_demand(_cfg())["rows"] == []
    monkeypatch.setattr(pa.storage, "load_latest_buy_list", lambda: {34: 5.0})
    assert pa.do_production_demand(_cfg())["rows"] == []


# ------------------------------------------------------------ skills
def test_characters_overview_esi_rows():
    rows = [{"owner_id": 1, "skill_id": 2495, "active_level": 5},
            {"owner_id": 1, "skill_id": 2505, "active_level": 4},
            {"owner_id": 1, "skill_id": 33467, "active_level": 3}]
    cfg = PiConfig()
    out = skills.characters_overview(cfg, [{"character_id": 1, "character_name": "A"}], rows)
    c = out["characters"][0]
    assert (c["planets"], c["cc_level"], c["customs_code_expertise"], c["source"]) == (6, 4, 3, "esi")
    assert out["total_slots"] == 6 and out["max_cc_level"] == 4 and out["character_count"] == 1


def test_characters_overview_manual_fallbacks():
    cfg = PiConfig(pi_planets_per_character=4, pi_cc_level=3, pi_customs_code_expertise_level=2, pi_characters=3)
    out = skills.characters_overview(cfg, [{"character_id": 1, "character_name": "A"},
                                           {"character_id": 2, "character_name": "B"}],
                                     [{"owner_id": 1, "skill_id": 2495, "active_level": 5}])
    a, b = out["characters"]
    assert a["source"] == "esi" and a["planets"] == 6 and a["cc_level"] == 0
    assert b["source"] == "manual" and (b["planets"], b["cc_level"], b["customs_code_expertise"]) == (4, 3, 2)
    assert out["total_slots"] == 10 and out["max_cc_level"] == 3

    none = skills.characters_overview(cfg, [], [])
    assert none["characters"] == [] and none["total_slots"] == 12 and none["character_count"] == 3
    assert none["max_cc_level"] == 3


def test_do_characters_delegates(monkeypatch):
    monkeypatch.setattr(skills, "load_overview", lambda cfg: {"total_slots": 9})
    assert pa.do_characters(_cfg()) == {"total_slots": 9}


# ------------------------------------------------------------ demand.unit_cost
def test_unit_cost_sanity(sd):
    coolant = _id(sd, "Coolant")
    chain = "P1-P2"
    ev, _ = engine.best_design(sd, pa.Planet(2016, 5000.0), chain, coolant, 5,
                               engine.Assumptions(2000, 72, 24))
    m = econ.MarketSettings(broker_fee=0.0, sales_tax=0.0, valuation="sell_orders", freight_per_m3=0.0,
                            tax_rate=0.0, amortisation_days=30, min_isk_per_planet_day=0.0,
                            market_share_warning=1.0, program_hours=72, interval_hours=24)
    e = econ.compute(ev, sd, _fake_prices(sd), m)
    cost = demand.unit_cost(ev, e)
    assert cost is not None and cost > 0
    # a free colony output of zero gives None
    from dataclasses import replace

    assert demand.unit_cost(ev, replace(e, output_units_per_day=0.0)) is None


# ------------------------------------------------------------ reference layouts
def test_generate_uses_a_reference_when_it_does_as_well(sd, monkeypatch):
    from eve_trader.pi.layout import reference

    real = reference.from_references
    seen = {}

    def spy(*a, **kw):
        seen["res"] = real(*a, **kw)
        return seen["res"]

    monkeypatch.setattr(reference, "from_references", spy)
    r = _gen(sd)
    assert r["source"] in ("reference", "generator")
    if r["source"] == "reference":
        assert any("community layout" in n for n in r["notes"])
        assert r["design"]["factories"] == [[t, n] for t, n in seen["res"].design.factories]
    assert r["analysis"]["ok"] is True


def test_generate_prefers_the_generator_when_it_is_clearly_better(sd, monkeypatch):
    from eve_trader.pi.layout import reference

    def weak(*a, **kw):
        res = reference.ReferenceResult.__new__(reference.ReferenceResult)
        res.effective_output = 1e-6
        return res

    monkeypatch.setattr(reference, "from_references", weak)
    assert _gen(sd)["source"] == "generator"


def test_generate_can_skip_references(sd, monkeypatch):
    from eve_trader.pi.layout import reference

    monkeypatch.setattr(reference, "from_references", lambda *a, **kw: pytest.fail("references used"))
    assert _gen(sd, use_references=False)["source"] == "generator"
    assert _gen(sd, shape="star")["source"] == "generator"

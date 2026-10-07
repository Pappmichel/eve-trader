"""PI actions and economics - pure. Static data comes from the SDE snapshot in
tests/fixtures/pi_static_rows.json; prices are fake, so no ESI or Postgres."""
import json
from pathlib import Path

import pytest

from eve_trader.actions import ActionError
from eve_trader.pi import actions as pa
from eve_trader.pi import constants as C
from eve_trader.pi import economics as econ
from eve_trader.pi import engine, static
from eve_trader.pi.config import PiConfig
from eve_trader.pi.model import Design, Planet

_ROWS = json.loads((Path(__file__).parent / "fixtures" / "pi_static_rows.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sd():
    return static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})


@pytest.fixture(autouse=True)
def _patched(monkeypatch, sd):
    engine.clear_cache()
    monkeypatch.setattr(pa, "_static", lambda: sd)
    monkeypatch.setattr(pa, "_medians_cache", {pt: 5000.0 for pt in C.PLANET_TYPE_IDS})
    monkeypatch.setattr(pa, "_prices", lambda s, cfg, with_history=True: _fake_prices(s))
    yield
    engine.clear_cache()


def _fake_prices(sd):
    # Higher tiers sell for more, so some chains are profitable.
    sell = {t: 100.0 * (10 ** c.tier) / 10 for t, c in sd.commodities.items()}
    return econ.Prices(sell=sell, buy={t: v * 0.9 for t, v in sell.items()})


def _id(sd, name):
    return next(t for t, c in sd.commodities.items() if c.name == name)


def _cfg(**over):
    return PiConfig(pi_min_isk_per_planet_day=0.0, pi_market_share_warning=1.0, **over)


# --------------------------------------------------------------- economics
@pytest.mark.parametrize("security,region,zone", [
    (0.45, 10000002, C.ZONE_HIGHSEC),   # float4 0.45 rounds to a high-sec 0.5
    (0.4500000059604645, 10000002, C.ZONE_HIGHSEC),
    (0.3, 10000002, C.ZONE_LOWSEC),
    (-0.1, 10000002, C.ZONE_NULLSEC),
    (-1.0, 11000005, C.ZONE_WORMHOLE),
    (-0.5, C.POCHVEN_REGION_ID, C.ZONE_NULLSEC),
])
def test_security_zone(security, region, zone):
    assert econ.security_zone(security, region) == zone


def test_npc_tax_rate():
    assert econ.npc_tax_rate(C.ZONE_HIGHSEC, 0) == pytest.approx(0.10)
    assert econ.npc_tax_rate(C.ZONE_HIGHSEC, 5) == pytest.approx(0.05)
    assert econ.npc_tax_rate(C.ZONE_LOWSEC, 0) == 0.0
    assert econ.npc_tax_rate(C.ZONE_NULLSEC, 5) == 0.0


def test_compute_export_tax_is_units_times_base_times_rate(sd):
    coolant = _id(sd, "Coolant")
    d = Design("P1-P2", coolant, 2016, 5, ((coolant, 24),), (), 2, 0)
    a = engine.Assumptions(yield_per_head=2000, program_hours=72, interval_hours=24)
    ev = engine.evaluate(sd, Planet(2016, 5000.0), d, a)
    m = econ.MarketSettings(
        broker_fee=0.0, sales_tax=0.0, valuation="sell_orders", freight_per_m3=0.0, tax_rate=0.10,
        amortisation_days=30, min_isk_per_planet_day=0.0, market_share_warning=1.0,
        program_hours=72, interval_hours=24,
    )
    e = econ.compute(ev, sd, _fake_prices(sd), m)
    units = ev.exports[coolant] * ev.effective_factor * 24
    assert e.export_tax_per_day == pytest.approx(units * 7200 * 0.10)
    assert e.output_units_per_day == pytest.approx(units)
    assert e.freight_per_day == 0.0
    assert e.revenue_per_day == pytest.approx(units * _fake_prices(sd).sell[coolant])


def test_unpriced_surplus_does_not_void_a_priced_product(sd):
    from dataclasses import replace

    coolant = _id(sd, "Coolant")
    p0 = next(t for t, c in sd.commodities.items() if c.tier == 0)
    d = Design("P1-P2", coolant, 2016, 5, ((coolant, 24),), (), 2, 0)
    a = engine.Assumptions(yield_per_head=2000, program_hours=72, interval_hours=24)
    ev = engine.evaluate(sd, Planet(2016, 5000.0), d, a)
    ev = replace(ev, exports={coolant: ev.exports[coolant], p0: 500.0})
    sell = dict(_fake_prices(sd).sell)
    sell[p0] = None
    m = econ.MarketSettings(
        broker_fee=0.0, sales_tax=0.0, valuation="sell_orders", freight_per_m3=10.0, tax_rate=0.10,
        amortisation_days=30, min_isk_per_planet_day=0.0, market_share_warning=1.0,
        program_hours=72, interval_hours=24,
    )
    e = econ.compute(ev, sd, econ.Prices(sell=sell, buy={}), m)
    assert e.unpriced_surplus == (p0,)
    assert e.missing_prices == ()
    assert e.reason != econ.REASON_NO_PRICE
    product_units = ev.exports[coolant] * ev.effective_factor * 24
    assert e.export_tax_per_day == pytest.approx(product_units * sd.commodities[coolant].export_tax_base * 0.10)
    assert e.byproduct_revenue_per_day == 0.0
    named = pa._economics_dict(e, sd)
    assert named["unpriced_surplus"] == [{"type_id": p0, "name": sd.name(p0)}]


def test_unit_cost_credits_priced_byproduct_revenue(sd):
    from dataclasses import replace

    from eve_trader.pi import demand

    coolant = _id(sd, "Coolant")
    water = _id(sd, "Water")
    d = Design("P1-P2", coolant, 2016, 5, ((coolant, 24),), (), 2, 0)
    a = engine.Assumptions(yield_per_head=2000, program_hours=72, interval_hours=24)
    base = engine.evaluate(sd, Planet(2016, 5000.0), d, a)
    ev = replace(base, exports={coolant: 10.0, water: 10000.0}, product_per_hour=10.0, imports={},
                 import_m3_per_hour=0.0)
    m = econ.MarketSettings(
        broker_fee=0.0, sales_tax=0.0, valuation="sell_orders", freight_per_m3=0.0, tax_rate=0.0,
        amortisation_days=30, min_isk_per_planet_day=0.0, market_share_warning=1.0,
        program_hours=72, interval_hours=24,
    )
    e = econ.compute(ev, sd, econ.Prices(sell={coolant: 1000.0, water: 1.0}, buy={}), m)
    f = ev.effective_factor * 24
    assert e.byproduct_revenue_per_day == pytest.approx(10000.0 * f)
    cost = demand.unit_cost(ev, e)
    costs = e.input_cost_per_day + e.export_tax_per_day + e.import_tax_per_day + e.freight_per_day + e.setup_per_day
    assert cost == pytest.approx((costs - e.byproduct_revenue_per_day) / e.output_units_per_day)
    # Quantity-share of total revenue would treat the bulky cheap surplus as
    # most of the colony's income and drive the product cost far below this.
    old_credit = e.revenue_per_day * (10000.0 / 10010.0)
    assert old_credit > e.byproduct_revenue_per_day * 1.5
    assert cost > (costs - old_credit) / e.output_units_per_day


# ----------------------------------------------------------- profitability
def test_profitability_rows_have_verdicts():
    out = pa.do_profitability(cfg=_cfg())
    assert out["zone"] == "highsec" and out["cc_level"] == 5
    rows = out["rows"]
    assert rows
    assert rows == sorted(rows, key=lambda r: -r["profit_per_day"])
    for r in rows:
        assert isinstance(r["worth_it"], bool)
        assert r["worth_it"] or r["reason"]
    assert {"P0-P1", "P1-P2"} <= {r["chain"] for r in rows}


def test_profitability_rejects_bad_zone_and_cc_level():
    with pytest.raises(ActionError):
        pa.do_profitability(zone="deepspace", cfg=_cfg())
    with pytest.raises(ActionError):
        pa.do_profitability(cc_level=9, cfg=_cfg())


# ----------------------------------------------------------------- planner
def test_planner_free_planet_type(sd):
    product = next(t for t in sd.products_of_tier(1) if engine.check_feasible(sd, 2016, "P0-P1", t) is None)
    out = pa.do_planner("P0-P1", product, planet_type_id=2016, radius_km=5000, cfg=_cfg())
    assert out["planet"]["planet_type_id"] == 2016 and out["planet"]["radius_km"] == 5000
    assert out["product"]["type_id"] == product
    assert out["evaluation"]["design"]["ecus"]
    assert "profit_per_day" in out["economics"]


def test_planner_explicit_design(sd):
    coolant = _id(sd, "Coolant")
    design = {"factories": [[coolant, 24]], "ecus": [], "launchpads": 2, "storages": 0}
    out = pa.do_planner("P1-P2", coolant, planet_type_id=2016, radius_km=5000, cc_level=5,
                        design=design, cfg=_cfg())
    assert out["evaluation"]["fits"] is True
    assert out["evaluation"]["design"]["factories"] == [[coolant, 24]]
    too_many = {**design, "factories": [[coolant, 25]]}
    out = pa.do_planner("P1-P2", coolant, planet_type_id=2016, radius_km=5000, cc_level=5,
                        design=too_many, cfg=_cfg())
    assert out["evaluation"]["fits"] is False


def test_planner_validation_errors(sd):
    coolant = _id(sd, "Coolant")
    with pytest.raises(ActionError, match="Unknown chain"):
        pa.do_planner("P9-P9", coolant, planet_type_id=2016, cfg=_cfg())
    with pytest.raises(ActionError):  # Coolant is not a P1 product
        pa.do_planner("P0-P1", coolant, planet_type_id=2016, cfg=_cfg())
    with pytest.raises(ActionError, match="planet"):
        pa.do_planner("P1-P2", coolant, cfg=_cfg())
    with pytest.raises(ActionError, match="radius"):
        pa.do_planner("P1-P2", coolant, planet_type_id=2016, radius_km=10, cfg=_cfg())
    with pytest.raises(ActionError):
        pa.do_planner("P1-P2", coolant, planet_type_id=2016, radius_km=5000,
                      design={"factories": [[coolant, 500]]}, cfg=_cfg())


# ------------------------------------------------------------------- plans
def _plan(**over):
    return {"name": "x", "planet_type_id": 2016,
            "design": {"chain": "P1-P2", "product_type_id": 1, "planet_type_id": 2016, "cc_level": 5,
                       "factories": [], "ecus": [], "launchpads": 1, "storages": 0}, **over}


@pytest.mark.parametrize("over,match", [
    ({"name": ""}, "name"),
    ({"name": "x" * 101}, "name"),
    ({"design": None}, "chain"),
    ({"design": {"chain": "nope"}}, "chain"),
    ({"planet_type_id": 1}, "planet type"),
    ({"owner_tax_rate": 2}, "owner_tax_rate"),
])
def test_save_plan_validation_errors(over, match):
    with pytest.raises(ActionError, match=match):
        pa.do_save_plan(_plan(**over))


def test_delete_unknown_plan(monkeypatch):
    monkeypatch.setattr(pa.storage, "delete_pi_plan", lambda plan_id: False)
    with pytest.raises(ActionError, match="Unknown plan"):
        pa.do_delete_plan(5)


# ---------------------------------------------------------------- settings
def test_update_settings_rejects_bad_input():
    cfg = PiConfig()
    with pytest.raises(ActionError, match="Unknown PI setting"):
        pa.do_update_settings({"not_a_field": 1}, cfg)
    with pytest.raises(ActionError, match="pi_zone"):
        pa.do_update_settings({"pi_zone": "deepspace"}, cfg)
    with pytest.raises(ActionError):
        pa.do_update_settings({"pi_sales_tax_rate": 1.5}, cfg)  # config._FIELD_RANGES bound


def test_get_settings_lists_every_field():
    s = pa.do_get_settings(PiConfig())
    assert s["pi_zone"] == "highsec" and s["hub_region_id"] == 10000002 and "pi_demand_days" in s

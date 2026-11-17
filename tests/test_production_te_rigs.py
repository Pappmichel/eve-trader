"""Separate time-efficiency rig tier, per-tier security tables, the Thukker
rig tier and the invention cost rig (engine.facility_resolution,
constants.structure_rig_multiplier, invention.fee_rates). No Postgres: the
category facilities and the security lookup are stubbed."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.production import actions as production_actions
from eve_trader.production import engine, invention
from eve_trader.production import facility_tax as facility_tax_module
from eve_trader.production.actions import ActionError
from eve_trader.production.config import ConfigError, ProductionConfig, validate_production_overrides
from eve_trader.production.constants import (
    ACTIVITY_MODS, SCC_SURCHARGE_RATE, STRUCTURE_TYPES, structure_rig_multiplier, tier_security_multiplier,
)

RAITARU = "Raitaru (M Engineering Complex)"
NULL_SEC, LOW_SEC, HIGH_SEC = -0.78, 0.3, 0.9


@pytest.fixture
def facilities(monkeypatch):
    data: dict = {}
    monkeypatch.setattr(engine, "_load_category_facilities", lambda: data)
    monkeypatch.setattr(engine, "_security_status_for", lambda profile, cfg: None)
    return data


def _cfg(**kwargs):
    base = dict(manufacturing_structure_type=RAITARU, manufacturing_rig_tier="T2-Rig")
    base.update(kwargs)
    return ProductionConfig(**base)


def _facility(security=NULL_SEC, **saved):
    row = {"structure_type": None, "rig_tier_basic": None, "rig_tier_advanced": None,
           "te_rig_tier_basic": None, "te_rig_tier_advanced": None, "location_id": 1,
           "structure_name": None, "system_id": 30000709, "security": security}
    row.update(saved)
    return row


def _mults(resolved):
    return structure_rig_multiplier(resolved["structure_type"], resolved["rig_tier"],
                                    resolved["security_multiplier"], resolved["te_rig_tier"],
                                    resolved["te_security_multiplier"])


def test_te_follows_me_without_an_own_te_tier(facilities):
    facilities["Equipment"] = _facility(rig_tier_basic="T2-Rig")
    resolved = engine.facility_resolution("Equipment", "Tech I", "manufacturing", _cfg())
    assert (resolved["te_rig_tier"], resolved["te_rig_source"]) == ("T2-Rig", "same as ME")
    # Identical to the pre-split single-tier computation.
    assert _mults(resolved) == structure_rig_multiplier(RAITARU, "T2-Rig", resolved["security_multiplier"])


def test_t2_me_with_no_rig_te_in_nullsec_keeps_me_and_drops_the_time_rig(facilities):
    facilities["Equipment"] = _facility(rig_tier_basic="T2-Rig", te_rig_tier_basic="No Rig")
    resolved = engine.facility_resolution("Equipment", "Tech I", "manufacturing", _cfg())
    assert (resolved["te_rig_tier"], resolved["te_rig_source"]) == ("No Rig", "set")
    _, me_mult, te_mult = _mults(resolved)
    structure = STRUCTURE_TYPES[RAITARU]
    assert resolved["security_multiplier"] == 2.1
    assert me_mult == pytest.approx(structure.me_multiplier * (1 - 0.024 * 2.1))
    assert te_mult == pytest.approx(structure.te_multiplier)  # structure bonus only


def test_global_te_field_applies_when_the_category_has_none(facilities):
    facilities["Equipment"] = _facility(rig_tier_basic="T2-Rig")
    resolved = engine.facility_resolution("Equipment", "Tech I", "manufacturing",
                                          _cfg(manufacturing_te_rig_tier="T1-Rig"))
    assert (resolved["te_rig_tier"], resolved["te_rig_source"]) == ("T1-Rig", "global")
    assert resolved["rig_tier"] == "T2-Rig"


def test_saved_te_tier_beats_the_global_te_field(facilities):
    facilities["Equipment"] = _facility(rig_tier_basic="T2-Rig", te_rig_tier_basic="No Rig")
    resolved = engine.facility_resolution("Equipment", "Tech I", "manufacturing",
                                          _cfg(manufacturing_te_rig_tier="T1-Rig"))
    assert (resolved["te_rig_tier"], resolved["te_rig_source"]) == ("No Rig", "set")


def test_global_te_applies_without_any_category_facility(facilities):
    resolved = engine.facility_resolution("Large Ships", "Tech I", "manufacturing",
                                          _cfg(manufacturing_te_rig_tier="No Rig"))
    assert (resolved["rig_tier"], resolved["rig_source"]) == ("T2-Rig", "global")
    assert (resolved["te_rig_tier"], resolved["te_rig_source"]) == ("No Rig", "global")


def test_advanced_te_tier_is_used_for_tech_ii_hulls(facilities):
    facilities["Large Ships"] = _facility(rig_tier_basic="T1-Rig", rig_tier_advanced="T2-Rig",
                                          te_rig_tier_advanced="No Rig")
    basic = engine.facility_resolution("Large Ships", "Tech I", "manufacturing", _cfg())
    advanced = engine.facility_resolution("Large Ships", "Tech II", "manufacturing", _cfg())
    assert basic["te_rig_tier"] == "T1-Rig" and basic["te_rig_source"] == "same as ME"
    assert advanced["te_rig_tier"] == "No Rig" and advanced["te_rig_source"] == "set"


def test_name_stays_a_me_source_only(facilities):
    facilities["Large Ships"] = _facility(structure_name="IAK-JW - CEZ T2 Adv L T1 Basic L")
    resolved = engine.facility_resolution("Large Ships", "Tech I", "manufacturing", _cfg())
    assert (resolved["rig_tier"], resolved["rig_source"]) == ("T1-Rig", "name")
    assert (resolved["te_rig_tier"], resolved["te_rig_source"]) == ("T1-Rig", "same as ME")


# ------------------------------------------------------------------ Thukker


@pytest.mark.parametrize("security, expected", [(HIGH_SEC, 0.1), (LOW_SEC, 1.9), (NULL_SEC, 0.1)])
def test_thukker_uses_its_own_security_table(facilities, security, expected):
    facilities["Equipment"] = _facility(security=security, rig_tier_basic="Thukker")
    resolved = engine.facility_resolution("Equipment", "Tech I", "manufacturing", _cfg())
    assert resolved["security_multiplier"] == expected
    _, me_mult, te_mult = _mults(resolved)
    structure = STRUCTURE_TYPES[RAITARU]
    assert me_mult == pytest.approx(structure.me_multiplier * (1 - 0.037 * expected))
    assert te_mult == pytest.approx(structure.te_multiplier)  # the Thukker rig has no time bonus


def test_standard_tiers_keep_the_standard_security_table():
    assert tier_security_multiplier("T2-Rig", LOW_SEC) == 1.9
    assert tier_security_multiplier("T2-Rig", NULL_SEC) == 2.1
    assert tier_security_multiplier("T2-Rig", NULL_SEC, is_reaction=True) == 1.1
    assert tier_security_multiplier("Thukker", None) == 0.1


def test_thukker_is_rejected_for_reactions(monkeypatch):
    monkeypatch.setattr(storage, "upsert_category_facility", lambda *a, **k: pytest.fail("saved"))
    with pytest.raises(ActionError):
        production_actions.do_set_category_facility("Reactions", rig_tier_basic="Thukker")
    with pytest.raises(ActionError):
        production_actions.do_set_category_facility("Reactions", te_rig_tier_basic="Thukker")
    with pytest.raises(ConfigError):
        validate_production_overrides({"reaction_rig_tier": "Thukker"})
    with pytest.raises(ConfigError):
        validate_production_overrides({"reaction_te_rig_tier": "Thukker"})
    validate_production_overrides({"component_rig_tier": "Thukker", "manufacturing_te_rig_tier": "Thukker"})


def test_te_settings_validation():
    validate_production_overrides({"manufacturing_te_rig_tier": None})
    validate_production_overrides({"manufacturing_te_rig_tier": "No Rig"})
    with pytest.raises(ConfigError):
        validate_production_overrides({"manufacturing_te_rig_tier": "T3-Rig"})
    with pytest.raises(ConfigError):
        validate_production_overrides({"invention_rig_tier": "T2-Rig"})
    validate_production_overrides({"invention_rig_tier": "Invention T2-Rig"})


def test_set_category_facility_saves_and_invalidates_te_tiers(monkeypatch):
    saved = {}
    monkeypatch.setattr(storage, "upsert_category_facility", lambda *args: saved.update(args=args))
    invalidated = []
    monkeypatch.setattr(production_actions, "invalidate_facility_cache", lambda: invalidated.append(1))
    monkeypatch.setattr(production_actions, "invalidate_discover_cache", lambda: None)
    result = production_actions.do_set_category_facility("Large Ships", rig_tier_basic="T2-Rig",
                                                         te_rig_tier_basic="No Rig",
                                                         te_rig_tier_advanced="T1-Rig")
    assert saved["args"] == ("Large Ships", None, "T2-Rig", None, None, "No Rig", "T1-Rig")
    assert invalidated == [1]
    assert result["te_rig_tier_basic"] == "No Rig"
    with pytest.raises(ActionError):  # only ship categories split by tech level
        production_actions.do_set_category_facility("Equipment", te_rig_tier_advanced="No Rig")


# -------------------------------------------------------- invention cost rig


class _Client:
    def get_adjusted_prices(self):
        return {34: 10.0}

    def get_system_cost_indices(self, system_id, activities=()):
        return {"invention": 0.05, "copying": 0.05}


@pytest.fixture
def modelled_invention(monkeypatch):
    from eve_trader import esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _Client)
    monkeypatch.setattr(invention, "_invention_system_id", lambda cfg: 30000709)
    monkeypatch.setattr(storage, "get_system_security", lambda system_id: NULL_SEC)
    monkeypatch.setattr(facility_tax_module, "observed_for_location", lambda *a: None)
    monkeypatch.setattr(facility_tax_module, "observed_total_for_location", lambda *a: None)


@pytest.mark.live_fee_rates
def test_invention_cost_rig_lowers_the_modelled_fee(modelled_invention):
    plain = invention.fee_rates(_cfg(manufacturing_structure_type=RAITARU, facility_tax_rate=0.0025))
    rigged = invention.fee_rates(_cfg(manufacturing_structure_type=RAITARU, facility_tax_rate=0.0025,
                                      invention_rig_tier="Invention T2-Rig"))
    cost = STRUCTURE_TYPES[RAITARU].cost_multiplier
    assert plain.invention_rate == pytest.approx(0.05 * cost + 0.0025 + SCC_SURCHARGE_RATE)
    # Null sec: 2.1 x -12 % on the structure-adjusted index; tax and SCC stay.
    assert rigged.invention_rate == pytest.approx(0.05 * cost * (1 - 0.12 * 2.1) + 0.0025 + SCC_SURCHARGE_RATE)
    assert rigged.copying_rate < plain.copying_rate


@pytest.mark.live_fee_rates
def test_observed_total_rate_ignores_the_invention_rig(modelled_invention, monkeypatch):
    class _Total:
        rate = 0.1538
    monkeypatch.setattr(facility_tax_module, "observed_total_for_location", lambda *a: _Total())
    rates = invention.fee_rates(_cfg(invention_rig_tier="Invention T2-Rig"))
    assert rates.invention_rate == 0.1538 and rates.copying_rate == 0.1538


@pytest.mark.live_fee_rates
def test_default_invention_rig_leaves_the_modelled_fee_unchanged(modelled_invention):
    cfg = _cfg(facility_tax_rate=0.0025)
    rates = invention.fee_rates(cfg)
    cost, _, _ = structure_rig_multiplier(cfg.manufacturing_structure_type, cfg.manufacturing_rig_tier)
    assert rates.invention_rate == pytest.approx(0.05 * cost + 0.0025 + SCC_SURCHARGE_RATE)
    assert ACTIVITY_MODS["Tech II"].job_cost_rate  # flat rate untouched, only used without indices

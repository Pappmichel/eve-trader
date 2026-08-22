"""Pure yield-formula tests for eve_trader/refining/engine.py - GitHub issue
#90. No Postgres needed - ore_ice_yield/scrapmetal_yield are plain
percentage-calculation functions over a RefiningConfig; see
test_storage_refining.py for apply_reprocessing_yield's SDE-backed
portion-rounding tests.
"""
import pytest

from eve_trader.refining.config import RefiningConfig
from eve_trader.refining.engine import ore_ice_base_yield, ore_ice_yield, scrapmetal_yield


def _max_ore_config() -> RefiningConfig:
    # Confirmed maximum: Tatara, T2-Rig, null-sec, max skills, RX-804 -> 90.6%
    return RefiningConfig(
        structure_type="Tatara (L Refinery)",
        rig_tier="T2-Rig",
        security_status=-0.5,  # any true-sec <= 0.0 rounds to null-sec (see production/constants.py)
        implant="RX-804",
        reprocessing_skill_level=5,
        reprocessing_efficiency_skill_level=5,
        ore_family_skill_levels={"Veldspar": 5},
    )


def test_ore_ice_yield_at_max_setup_is_close_to_the_confirmed_90_6_percent_ceiling():
    # Base(Tatara, T2-Rig, null-sec) = 52% + 5% x 2.1 = 62.5% (back-solved
    # from - and confirmed against - the real 90.6% ceiling, see
    # constants.py's module docstring) x 1.15 x 1.10 x 1.10 x 1.04 = 90.4475%.
    # Not byte-exact to the real 90.6% (a ~0.15pp gap, plausibly per-step
    # rounding in the real game client) - approximated Base constants,
    # confirmed acceptable with the user rather than the exact live formula.
    cfg = _max_ore_config()
    assert ore_ice_yield(cfg, "Veldspar") == pytest.approx(0.9045, abs=0.0005)
    assert ore_ice_yield(cfg, "Veldspar") == pytest.approx(0.906, abs=0.002)


def test_ore_ice_yield_zero_skills_and_no_bonuses_equals_bare_structure_base():
    cfg = RefiningConfig()  # every default: Citadel, No Rig, no implant, no skills
    assert ore_ice_yield(cfg, None) == pytest.approx(0.50)


def test_ore_ice_yield_unknown_or_missing_ore_family_is_treated_as_unskilled():
    cfg = _max_ore_config()
    # "Veldspar" is the only family with a stored skill level - a different
    # family (never entered) or None (item has no known family at all) must
    # not silently borrow Veldspar's level 5.
    assert ore_ice_yield(cfg, "Scordite") < ore_ice_yield(cfg, "Veldspar")
    assert ore_ice_yield(cfg, None) < ore_ice_yield(cfg, "Veldspar")


def test_ore_ice_base_yield_rig_bonus_is_scaled_by_security():
    highsec = RefiningConfig(structure_type="Tatara (L Refinery)", rig_tier="T2-Rig", security_status=0.9)
    nullsec = RefiningConfig(structure_type="Tatara (L Refinery)", rig_tier="T2-Rig", security_status=-1.0)
    assert ore_ice_base_yield(nullsec) > ore_ice_base_yield(highsec)


def test_scrapmetal_yield_hits_confirmed_maximum_of_55_percent():
    cfg = RefiningConfig(scrapmetal_processing_skill_level=5)
    assert scrapmetal_yield(cfg) == pytest.approx(0.55)


def test_scrapmetal_yield_default_is_the_fixed_50_percent_base():
    cfg = RefiningConfig()
    assert scrapmetal_yield(cfg) == pytest.approx(0.50)


def test_scrapmetal_yield_ignores_structure_rig_implant_and_ore_path_skills():
    # Confirmed asymmetry vs. the ore/ice path (constants.py's module
    # docstring): structure/rig/security/implant/the two ore-path skills have
    # NO effect on the scrapmetal path.
    baseline = RefiningConfig(scrapmetal_processing_skill_level=3)
    loaded = RefiningConfig(
        scrapmetal_processing_skill_level=3,
        structure_type="Tatara (L Refinery)", rig_tier="T2-Rig", security_status=-1.0,
        implant="RX-804", reprocessing_skill_level=5, reprocessing_efficiency_skill_level=5,
    )
    assert scrapmetal_yield(baseline) == scrapmetal_yield(loaded)


def test_ore_ice_yield_clamps_out_of_range_skill_levels():
    # A bad config.yaml/Settings value (e.g. a typo'd 15) must never inflate
    # a yield past what's actually achievable in-game - clamp_skill_level
    # bounds every skill to EVE's real 0-5 range.
    over_maxed = RefiningConfig(reprocessing_skill_level=15, reprocessing_efficiency_skill_level=15,
                                 ore_family_skill_levels={"Veldspar": 15})
    maxed = RefiningConfig(reprocessing_skill_level=5, reprocessing_efficiency_skill_level=5,
                            ore_family_skill_levels={"Veldspar": 5})
    assert ore_ice_yield(over_maxed, "Veldspar") == ore_ice_yield(maxed, "Veldspar")

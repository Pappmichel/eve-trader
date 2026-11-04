"""Per-category structure rigs: name parser and engine resolution."""
import pytest

from eve_trader import storage
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.facility_names import rig_tiers_from_name

T1, T2 = "T1-Rig", "T2-Rig"


@pytest.mark.parametrize("name,category,expected", [
    # Default tenant's real structure names.
    ("IAK-JW - CEZ T2 Adv L T1 Basic L", "Large Ships", (T1, T2)),
    ("IAK-JW - CEZ T2 Adv M T1 Basic M", "Medium Ships", (T1, T2)),
    ("IAK-JW - CEZ T2 Adv S T1 Basic S", "Small Ships", (T1, T2)),
    ("IAK-JW - CEZ T2 Equip", "Equipment", (T2, T2)),
    ("L-FM3P - T2 mods T1 fighters & drones", "Drones & Fighters", (T1, T1)),
    ("L-FM3P - T2 mods T1 fighters & drones", "Equipment", (T2, T2)),
    ("L5-UWT - CEZ T1 Capitals", "Capital Ship", (T1, T1)),
    ("O-7LAI - GEZ T1 Cap Supers", "Super Capital Ship", (T1, T1)),
    ("6-L4YC - KFEZ Adv Comp", "Advanced Components", (None, None)),
    ("M3-KAQ - KFEZ Cap Comp", "Capital Components", (None, None)),
    ("6-L4YC - KFEZ Reactions", "Reactions", (None, None)),
    # Second tenant's names.
    ("L-FM3P - T2 basic L", "Large Ships", (T2, None)),
    ("L-FM3P - T1 Adv M L Ships, T2 adv comps", "Medium Ships", (None, T1)),
    ("L-FM3P - T1 Adv M L Ships, T2 adv comps", "Large Ships", (None, T1)),
    ("L-FM3P - T1 Adv M L Ships, T2 adv comps", "Advanced Components", (T2, T2)),
    ("L-FM3P - T2 adv S", "Small Ships", (None, T2)),
    ("SJJ-4F - Hari's workshop", "Capital Components", (None, None)),
    ("B9E-H6 - GEZ T1 Ship Comp Equip", "Capital Ship", (None, None)),
    # One tier, no category words: applies to everything.
    ("XYZ - T2", "Large Ships", (T2, T2)),
    (None, "Large Ships", (None, None)),
])
def test_rig_tiers_from_name(name, category, expected):
    assert rig_tiers_from_name(name, category) == expected


@pytest.fixture
def facilities(monkeypatch):
    data: dict = {}
    monkeypatch.setattr(engine, "_load_category_facilities", lambda: data)
    monkeypatch.setattr(engine, "_security_multiplier_for", lambda profile, cfg: 1.0)
    return data


def _cfg():
    return ProductionConfig(manufacturing_structure_type="Raitaru (M Engineering Complex)",
                            manufacturing_rig_tier="T2-Rig")


def _facility(name, security=-0.78, **saved):
    return {"structure_type": saved.get("structure_type"), "rig_tier_basic": saved.get("rig_tier_basic"),
            "rig_tier_advanced": saved.get("rig_tier_advanced"), "location_id": 1, "structure_name": name,
            "system_id": 30000709, "security": security}


def test_typhoon_uses_the_basic_large_ship_rig_from_the_structure_name(facilities):
    facilities["Large Ships"] = _facility("IAK-JW - CEZ T2 Adv L T1 Basic L")
    resolved = engine.facility_resolution("Large Ships", "Tech I", "manufacturing", _cfg())
    assert (resolved["rig_tier"], resolved["rig_source"]) == ("T1-Rig", "name")
    assert resolved["security_multiplier"] == 2.1
    _, me_mult, _ = engine.structure_rig_multiplier(resolved["structure_type"], resolved["rig_tier"],
                                                    resolved["security_multiplier"])
    # ME10 Typhoon blueprint, 166 runs: 5,200,000 Tritanium per run.
    assert engine._material_qty(5_200_000, 0.9 * me_mult, 166) == 736_808_530


def test_tech_ii_hull_uses_the_advanced_rig(facilities):
    facilities["Large Ships"] = _facility("IAK-JW - CEZ T2 Adv L T1 Basic L")
    resolved = engine.facility_resolution("Large Ships", "Tech II", "manufacturing", _cfg())
    assert resolved["rig_tier"] == "T2-Rig"


def test_saved_tier_beats_the_name(facilities):
    facilities["Large Ships"] = _facility("IAK-JW - CEZ T2 Adv L T1 Basic L", rig_tier_basic="No Rig",
                                          structure_type="Azbel (L Engineering Complex)")
    resolved = engine.facility_resolution("Large Ships", "Tech I", "manufacturing", _cfg())
    assert (resolved["rig_tier"], resolved["rig_source"]) == ("No Rig", "set")
    assert (resolved["structure_type"], resolved["structure_source"]) == ("Azbel (L Engineering Complex)", "set")


def test_non_ship_category_uses_one_rig_for_every_tech_level(facilities):
    facilities["Equipment"] = _facility("X - Y", rig_tier_basic="T1-Rig")
    resolved = engine.facility_resolution("Equipment", "Tech II", "manufacturing", _cfg())
    assert (resolved["rig_tier"], resolved["rig_source"]) == ("T1-Rig", "set")


def test_name_without_tier_falls_back_to_the_global_profile(facilities):
    facilities["Reactions"] = _facility("6-L4YC - KFEZ Reactions", security=-0.24)
    cfg = ProductionConfig(reaction_structure_type="Tatara (L Refinery)", reaction_rig_tier="T1-Rig")
    resolved = engine.facility_resolution("Reactions", "Reaction", "reaction", cfg)
    assert (resolved["rig_tier"], resolved["rig_source"]) == ("T1-Rig", "global")
    assert resolved["security_multiplier"] == 1.1  # refinery table, null-sec


def test_category_without_facility_keeps_the_global_behaviour(facilities):
    resolved = engine.facility_resolution("Large Ships", "Tech I", "manufacturing", _cfg())
    assert (resolved["structure_type"], resolved["rig_tier"], resolved["security_multiplier"]) == (
        "Raitaru (M Engineering Complex)", "T2-Rig", 1.0)


def test_no_tenant_means_no_facilities(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: None)
    assert engine._load_category_facilities() == {}

from eve_trader import storage
from eve_trader.candidate_discovery import (
    BOOSTER_GROUP_ID, IMPLANT_CATEGORY_ID, MODULE_CATEGORY_ID, _build_candidate_universe_from_sde,
    build_focused_candidate_universe, guess_category, is_wanted_market_path,
)
from eve_trader.config import TradingConfig
from eve_trader.models import Candidate


def test_guess_category_prefers_real_category_id():
    # A material with a misleading name/path ("Modular Something") used to be
    # wrongly bucketed as "Module/Rig" by the old string-matching heuristic -
    # a real category_id must override that.
    assert guess_category("Materials > Whatever", "Modular Something", 0.1,
                           category_id=18) == "Material"  # 18 = Drone, not Module
    assert guess_category("Ship Equipment > Rigs", "Rig Whatever", 0.1,
                           category_id=MODULE_CATEGORY_ID) == "Module/Rig"


def test_guess_category_uses_real_sde_category_name_when_available():
    # Confirmed wrong with the user: implants (category_id 20) used to be
    # bucketed into the catch-all "Material" label since only category_id==7
    # (Module) got its own name. With the real name map (storage.
    # load_sde_category_names, from Fuzzwork's invCategories.csv) available,
    # every category gets its actual EVE name instead of a binary guess.
    category_names = {7: "Module", 18: "Drone", 20: "Implant", 8: "Charge"}
    assert guess_category("Pilot's Services > Implants", "Ocular Filter", 0.1,
                           category_id=20, category_names=category_names) == "Implant"
    assert guess_category("Drones", "Hobgoblin I", 5.0,
                           category_id=18, category_names=category_names) == "Drone"
    assert guess_category("Ship Equipment > Rigs", "Rig Whatever", 0.1,
                           category_id=MODULE_CATEGORY_ID, category_names=category_names) == "Module"
    # An unmapped category_id (not in the passed-in dict) falls back to the
    # old binary split rather than crashing.
    assert guess_category("Materials > Whatever", "Something", 0.1,
                           category_id=999, category_names=category_names) == "Material"


def test_guess_category_splits_boosters_out_of_implant():
    # Boosters/Drugs share category_id 20 "Implant" with real cyberimplants
    # in the SDE - only group_id (303 "Booster") tells them apart. Confirmed
    # wrong labeling (both showing as "Implant") by the user.
    category_names = {IMPLANT_CATEGORY_ID: "Implant"}
    assert guess_category("Pilot's Services > Boosters", "Blue Pill", 0.1,
                           category_id=IMPLANT_CATEGORY_ID, category_names=category_names,
                           group_id=BOOSTER_GROUP_ID) == "Drugs"
    # A real cyberimplant (same category_id, different group_id) still gets
    # the real SDE category name, unaffected.
    assert guess_category("Pilot's Services > Implants", "Ocular Filter", 0.1,
                           category_id=IMPLANT_CATEGORY_ID, category_names=category_names,
                           group_id=300) == "Implant"


def test_guess_category_falls_back_to_string_heuristic_without_category_id():
    # Rare live-ESI-walk path (_build_candidate_universe_from_esi) doesn't
    # resolve category_id - falls back to the old heuristic.
    assert guess_category("Ship Equipment > Rigs", "Some Rig I", 1.0) == "Module/Rig"
    assert guess_category("Materials", "Tritanium", 0.01) == "Material"
    assert guess_category("Materials", "Heavy Thing", 10.0) == "Module/Rig"


def test_is_wanted_market_path_only_excludes_the_confirmed_categories():
    cfg = TradingConfig()
    # Confirmed excluded with the user - structurally don't fit the
    # Jita->C-J import-arbitrage model.
    assert is_wanted_market_path("Ships > Frigates", cfg) is False
    assert is_wanted_market_path("Blueprints > Ship Blueprints", cfg) is False
    assert is_wanted_market_path("Apparel > Male", cfg) is False
    assert is_wanted_market_path("Personalization > SKINs", cfg) is False
    assert is_wanted_market_path("Pilot's Services > Jump Clones", cfg) is False
    assert is_wanted_market_path("Structures > Citadels", cfg) is False


def test_is_wanted_market_path_no_longer_requires_an_allowlist_match():
    # Confirmed with the user: "Skills" is included now (used to be
    # excluded), and there's no more keyword allowlist gating everything
    # else - any path not in excluded_path_prefixes passes, e.g. a category
    # with no historical keyword match at all ("Ice Products" used to need
    # its own explicit keyword; now it needs nothing extra).
    cfg = TradingConfig()
    assert is_wanted_market_path("Skills > Spaceship Command", cfg) is True
    assert is_wanted_market_path("Manufacture & Research > Materials > Ice Products", cfg) is True
    assert is_wanted_market_path("Some Brand New Category CCP Adds Later", cfg) is True


def test_build_candidate_universe_from_sde_uses_packaged_volume_for_capital_modules(monkeypatch):
    # GitHub issue #73: the SDE-backed candidate universe (the normal path -
    # used whenever Production's SDE refresh has been run) used to set
    # Candidate.volume_m3 straight from the raw SDE `volume` column, which
    # badly overstates freight cost for capital-sized modules (confirmed live
    # via ESI, same quirk as production/engine.py's _haul_volume/issue #11:
    # Capital Shield Booster I lists volume=4000 but packaged_volume=1000).
    # Ships have the identical quirk but are excluded from candidates
    # entirely (excluded_path_prefixes), so only capital modules hit this.
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: 1000.0)

    market_groups = [(1, None, "Ship Equipment")]
    sde_types = [
        # (type_id, type_name, volume, market_group_id, meta_level, category_id)
        (20703, "Capital Shield Booster I", 4000.0, 1, 0, MODULE_CATEGORY_ID),
    ]
    candidates = _build_candidate_universe_from_sde(market_groups, sde_types, TradingConfig())

    assert len(candidates) == 1
    assert candidates[0].volume_m3 == 1000.0


def test_build_candidate_universe_from_sde_leaves_ordinary_modules_unchanged(monkeypatch):
    # An ordinary (non-capital) module has packaged == flight volume - the
    # cache lookup still runs (no clean SDE-only signal for which modules
    # differ, same as production/engine.py's own _haul_volume), but returns
    # the same value candidate_universe already had.
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: 5.0)

    market_groups = [(1, None, "Ship Equipment")]
    sde_types = [(1234, "Large Shield Booster I", 5.0, 1, 0, MODULE_CATEGORY_ID)]
    candidates = _build_candidate_universe_from_sde(market_groups, sde_types, TradingConfig())

    assert candidates[0].volume_m3 == 5.0


def test_build_candidate_universe_from_sde_leaves_non_module_categories_unchanged():
    # Materials/etc. never hit the packaged-volume lookup at all (no
    # storage.get_cached_packaged_volume monkeypatch here - a real call would
    # error under the test DB, proving resolve_effective_volume short-circuits
    # before reaching it for non-Ship/Module categories).
    market_groups = [(1, None, "Materials")]
    sde_types = [(34, "Tritanium", 0.01, 1, 0, 4)]  # category_id 4 = Material
    candidates = _build_candidate_universe_from_sde(market_groups, sde_types, TradingConfig())

    assert candidates[0].volume_m3 == 0.01


def test_build_focused_candidate_universe_is_a_pass_through():
    # Confirmed with the user: no more per-item keyword/volume(m3) filtering -
    # profitability + trading volume are checked later, in the backtest.
    universe = [
        Candidate(item="Huge Bulky Ship Part", type_id=1, volume_m3=5000.0,
                  category="Module/Rig", market_group_path="Ship Equipment > Whatever"),
        Candidate(item="Tiny Cheap Thing", type_id=2, volume_m3=0.01,
                  category="Material", market_group_path="Materials > Whatever"),
    ]
    assert build_focused_candidate_universe(universe, TradingConfig()) == universe

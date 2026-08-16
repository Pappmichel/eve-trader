from eve_trader.candidate_discovery import (
    MODULE_CATEGORY_ID, build_focused_candidate_universe, guess_category, is_wanted_market_path,
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

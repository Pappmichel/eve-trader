"""Tests for eve_trader/refining/candidate_discovery.py - GitHub issue #91.
Pure/no Postgres needed - storage.load_ore_ice_candidate_types is
monkeypatched."""
from eve_trader import storage
from eve_trader.refining.candidate_discovery import build_ore_candidate_universe, ore_ice_families_for_types


def test_ore_family_derived_from_the_per_family_group_name(monkeypatch):
    # Real SDE: a compressed ore type shares its raw ore's own group, so
    # storage.load_ore_ice_candidate_types returns the bare family name
    # ("Veldspar"), not "Compressed Veldspar" - see storage.py's own
    # docstring for how this was confirmed live.
    monkeypatch.setattr(storage, "load_ore_ice_candidate_types", lambda: [
        (34, "Compressed Veldspar", 0.01, "Veldspar"),
    ])
    candidates = build_ore_candidate_universe()
    assert len(candidates) == 1
    assert candidates[0].family == "Veldspar"
    assert candidates[0].is_ice is False


def test_ice_family_derived_from_the_type_name_not_the_shared_group_name(monkeypatch):
    monkeypatch.setattr(storage, "load_ore_ice_candidate_types", lambda: [
        (16263, "Compressed Blue Ice", 0.4, "Ice"),
        (16264, "Compressed Clear Icicle", 0.4, "Ice"),
    ])
    candidates = build_ore_candidate_universe()
    families = {c.item: c.family for c in candidates}
    assert families == {"Compressed Blue Ice": "Blue Ice", "Compressed Clear Icicle": "Clear Icicle"}
    assert all(c.is_ice for c in candidates)


def test_skips_rows_with_no_name_or_volume(monkeypatch):
    monkeypatch.setattr(storage, "load_ore_ice_candidate_types", lambda: [
        (1, "", 0.01, "Veldspar"),
        (2, "Compressed Scordite", 0.0, "Scordite"),
        (3, "Compressed Scordite", 0.01, "Scordite"),
    ])
    candidates = build_ore_candidate_universe()
    assert len(candidates) == 1
    assert candidates[0].type_id == 3


def test_ore_ice_families_for_types_covers_raw_and_compressed_and_excludes_non_ore(monkeypatch):
    """T2-01: unlike build_ore_candidate_universe (compressed types only, via
    the fixed load_ore_ice_candidate_types scan), this resolves arbitrary
    type_ids for the Reprocessing tab's paste-import - raw ore (no
    "Compressed " prefix at all) must classify correctly too, and a non-ore
    item must be excluded from the result rather than raising or defaulting
    to some family."""
    monkeypatch.setattr(storage, "get_types_names_and_groups_bulk", lambda type_ids: {
        20: ("Veldspar", "Veldspar", 25),                    # raw ore
        34: ("Compressed Veldspar", "Veldspar", 25),         # compressed ore, same group
        16263: ("Compressed Blue Ice", "Ice", 25),           # compressed ice, shared group
        100: ("Antimatter Charge S", "Antimatter Charge", 8),  # not ore/ice (category_id 8 = Charge)
    })

    result = ore_ice_families_for_types([20, 34, 16263, 100])

    assert result[20] == ("Veldspar", False)
    assert result[34] == ("Veldspar", False)
    assert result[16263] == ("Blue Ice", True)
    assert 100 not in result

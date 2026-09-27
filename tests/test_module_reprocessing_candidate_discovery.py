"""Tests for eve_trader/module_reprocessing/candidate_discovery.py. Pure/no
Postgres needed - storage.module_reprocessing_candidate_types is
monkeypatched (the real SQL exclusion filter itself - category_id/
meta_group_id/rig-slot - is covered by tests/test_storage_module_
reprocessing.py, which needs a real Postgres connection)."""
from eve_trader import storage
from eve_trader.module_reprocessing.candidate_discovery import build_module_candidate_universe


def test_builds_candidates_from_storage_rows(monkeypatch):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [
        (100, "200mm AutoCannon I", 0.01),
        (200, "Small Shield Extender I", 0.005),
    ])
    candidates = build_module_candidate_universe()
    assert len(candidates) == 2
    assert candidates[0].type_id == 100
    assert candidates[0].item == "200mm AutoCannon I"
    assert candidates[0].volume_m3 == 0.01


def test_skips_rows_with_no_name_or_volume(monkeypatch):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [
        (1, "", 0.01),
        (2, "Some Module I", 0.0),
        (3, "Some Module II", None),
        (4, "Real Module I", 0.02),
    ])
    candidates = build_module_candidate_universe()
    assert len(candidates) == 1
    assert candidates[0].type_id == 4


def test_empty_universe_returns_empty_list(monkeypatch):
    monkeypatch.setattr(storage, "module_reprocessing_candidate_types", lambda: [])
    assert build_module_candidate_universe() == []

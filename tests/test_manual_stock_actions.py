"""Tests for production/actions.py's manual-stock-entries do_* wrappers
(docs/MANUAL_TRACKING_PLAN.md phase 3) - unit-level, storage monkeypatched,
no Postgres needed. See test_manual_stock_entries.py for storage.py's own
per-location functions."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions

TYPE_ID = 34  # Tritanium
LOCATION_A = 1000000000001


def test_do_set_manual_stock_rejects_negative_count():
    with pytest.raises(ActionError, match="cannot be negative"):
        actions.do_set_manual_stock(TYPE_ID, -1)


def test_do_set_manual_stock_passes_location_id_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "upsert_manual_stock",
                         lambda type_id, count, location_id=0: captured.update(
                             type_id=type_id, count=count, location_id=location_id))

    result = actions.do_set_manual_stock(TYPE_ID, 100, location_id=LOCATION_A)

    assert captured == {"type_id": TYPE_ID, "count": 100, "location_id": LOCATION_A}
    assert result == {"type_id": TYPE_ID, "count": 100, "location_id": LOCATION_A}


def test_do_set_manual_stock_defaults_location_id_to_zero(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "upsert_manual_stock",
                         lambda type_id, count, location_id=0: captured.update(location_id=location_id))

    actions.do_set_manual_stock(TYPE_ID, 100)

    assert captured == {"location_id": 0}


def test_do_list_manual_stock_entries_maps_rows_to_dicts(monkeypatch):
    monkeypatch.setattr(storage, "load_manual_stock_entries",
                         lambda: [(TYPE_ID, "Tritanium", LOCATION_A, 100.0)])

    result = actions.do_list_manual_stock_entries()

    assert result == {"rows": [
        {"type_id": TYPE_ID, "type_name": "Tritanium", "location_id": LOCATION_A, "count": 100.0},
    ]}


def test_do_add_manual_stock_entry_resolves_exact_name(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [(TYPE_ID, "Tritanium")])
    captured = {}
    monkeypatch.setattr(storage, "upsert_manual_stock",
                         lambda type_id, count, location_id=0: captured.update(
                             type_id=type_id, count=count, location_id=location_id))

    result = actions.do_add_manual_stock_entry("Tritanium", 100, LOCATION_A)

    assert captured == {"type_id": TYPE_ID, "count": 100, "location_id": LOCATION_A}
    assert result == {"type_id": TYPE_ID, "type_name": "Tritanium", "location_id": LOCATION_A, "count": 100}


def test_do_add_manual_stock_entry_rejects_negative_count(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [(TYPE_ID, "Tritanium")])
    with pytest.raises(ActionError, match="cannot be negative"):
        actions.do_add_manual_stock_entry("Tritanium", -1, LOCATION_A)


def test_do_add_manual_stock_entry_rejects_unknown_name(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [])
    with pytest.raises(ActionError, match="No type found"):
        actions.do_add_manual_stock_entry("Not A Real Item", 1, 0)


def test_do_add_manual_stock_entry_rejects_ambiguous_name(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda query, limit=20: [(99, "Tritanium Ore Sample")])
    with pytest.raises(ActionError, match="Did you mean"):
        actions.do_add_manual_stock_entry("Tritanium", 1, 0)


def test_do_remove_manual_stock_entry_passes_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "delete_manual_stock",
                         lambda type_id, location_id=0: captured.update(type_id=type_id, location_id=location_id))

    result = actions.do_remove_manual_stock_entry(TYPE_ID, LOCATION_A)

    assert captured == {"type_id": TYPE_ID, "location_id": LOCATION_A}
    assert result == {"type_id": TYPE_ID, "location_id": LOCATION_A}


# ------------------------------------------------------------- asset paste

def _stub_resolution(monkeypatch, exact: dict, suggestions: dict | None = None):
    monkeypatch.setattr(storage, "resolve_type_names_exact", lambda names: exact)
    monkeypatch.setattr(storage, "suggest_type_names", lambda names: suggestions or {})


def test_parse_asset_paste_skips_blueprint_lines(monkeypatch):
    _stub_resolution(monkeypatch, {"tritanium": (34, "Tritanium", 4)})
    text = "Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t\nRifter Blueprint\t1\tFrigate BP\tBlueprint\t\t\t0.01 m3\t\t"

    result = actions._parse_asset_paste(text)

    assert result["resolved"] == {34: ("Tritanium", 100.0)}
    assert result["skipped_blueprints"] == ["Rifter Blueprint"]
    assert result["unresolved"] == []
    assert result["errors"] == []


def test_parse_asset_paste_reports_unresolved_with_suggestion(monkeypatch):
    _stub_resolution(monkeypatch, {}, {"tritanum": (34, "Tritanium")})
    text = "Tritanum\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t"

    result = actions._parse_asset_paste(text)

    assert result["resolved"] == {}
    assert result["unresolved"] == [{"line": text, "suggestion": "Tritanium"}]


def test_parse_asset_paste_unresolved_without_suggestion_is_none(monkeypatch):
    _stub_resolution(monkeypatch, {}, {})
    text = "Not A Real Item\t1\tJunk\tMaterial\t\t\t0.01 m3\t\t"

    result = actions._parse_asset_paste(text)

    assert result["unresolved"] == [{"line": text, "suggestion": None}]


def test_parse_asset_paste_reports_parse_errors():
    result = actions._parse_asset_paste("not tab separated free text")

    assert result["resolved"] == {}
    assert len(result["errors"]) == 1
    assert result["errors"][0]["line"] == "not tab separated free text"


def test_do_preview_asset_paste_rejects_bad_mode():
    with pytest.raises(ActionError, match="Mode must be"):
        actions.do_preview_asset_paste("Tritanium\t1\tMineral\tMaterial\t\t\t\t\t", 0, "bogus")


def test_do_preview_asset_paste_rejects_empty_text():
    with pytest.raises(ActionError, match="empty"):
        actions.do_preview_asset_paste("   ", 0, "merge")


def test_do_preview_asset_paste_marks_new_row(monkeypatch):
    _stub_resolution(monkeypatch, {"tritanium": (34, "Tritanium", 4)})
    monkeypatch.setattr(storage, "load_manual_stock_entries", lambda: [])

    result = actions.do_preview_asset_paste("Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t", LOCATION_A, "merge")

    assert result["rows"] == [{"type_id": 34, "name": "Tritanium", "old": 0.0, "new": 100.0, "status": "new"}]


def test_do_preview_asset_paste_merge_adds_to_existing(monkeypatch):
    _stub_resolution(monkeypatch, {"tritanium": (34, "Tritanium", 4)})
    monkeypatch.setattr(storage, "load_manual_stock_entries",
                         lambda: [(34, "Tritanium", LOCATION_A, 50.0), (34, "Tritanium", 999, 10.0)])

    result = actions.do_preview_asset_paste("Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t", LOCATION_A, "merge")

    assert result["rows"] == [{"type_id": 34, "name": "Tritanium", "old": 50.0, "new": 150.0, "status": "changed"}]


def test_do_preview_asset_paste_replace_marks_missing_rows_removed(monkeypatch):
    _stub_resolution(monkeypatch, {"tritanium": (34, "Tritanium", 4)})
    monkeypatch.setattr(storage, "load_manual_stock_entries",
                         lambda: [(34, "Tritanium", LOCATION_A, 50.0), (35, "Pyerite", LOCATION_A, 20.0)])

    result = actions.do_preview_asset_paste("Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t", LOCATION_A, "replace")

    by_type = {r["type_id"]: r for r in result["rows"]}
    assert by_type[34] == {"type_id": 34, "name": "Tritanium", "old": 50.0, "new": 100.0, "status": "changed"}
    assert by_type[35] == {"type_id": 35, "name": "Pyerite", "old": 20.0, "new": 0.0, "status": "removed"}


def test_do_preview_asset_paste_merge_does_not_mark_other_locations_removed(monkeypatch):
    # Only rows at the *target* location are diffed - a row at a different
    # location is neither touched nor reported.
    _stub_resolution(monkeypatch, {})
    monkeypatch.setattr(storage, "load_manual_stock_entries", lambda: [(35, "Pyerite", 999, 20.0)])

    result = actions.do_preview_asset_paste("Something\t1\tJunk\tMaterial\t\t\t\t\t", LOCATION_A, "replace")

    assert result["rows"] == []


def test_do_commit_asset_paste_calls_apply_with_resolved_rows(monkeypatch):
    _stub_resolution(monkeypatch, {"tritanium": (34, "Tritanium", 4)})
    captured = {}
    monkeypatch.setattr(storage, "apply_manual_stock_paste",
                         lambda location_id, rows, mode: captured.update(location_id=location_id, rows=rows, mode=mode))

    result = actions.do_commit_asset_paste("Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t", LOCATION_A, "merge")

    assert captured == {"location_id": LOCATION_A, "rows": {34: 100.0}, "mode": "merge"}
    assert result["applied"] == 1


def test_do_commit_asset_paste_rejects_bad_mode():
    with pytest.raises(ActionError, match="Mode must be"):
        actions.do_commit_asset_paste("Tritanium\t1\tMineral\tMaterial\t\t\t\t\t", 0, "bogus")

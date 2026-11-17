"""Rig tiers from a shared sheet: parser, fetch guard, storage and resolution.

Every sheet below is invented (fantasy systems, a made-up sheet id): the real
sheet's address and contents never belong in the repository.
"""
from __future__ import annotations

import csv
import io

import pytest
import requests

from eve_trader import storage
from eve_trader.production import actions, engine, rig_sheet
from eve_trader.production.config import ConfigError, ProductionConfig, validate_production_overrides

from . import pg_helpers
from .pg_helpers import _apply_job_category_cost_index_overrides_schema, _apply_phase1_schema, tenant  # noqa: F401

T1, T2, NO = "T1-Rig", "T2-Rig", "No Rig"
FAKE_URL = "https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/edit?usp=sharing#gid=42"

# (group, column) in the order of the sheet's column row.
COLUMNS = (
    [("", c) for c in ("Location", "Description", "Largest Structure", "Owner", "Region", "Distance (ly)",
                       "Manufacturing", "Reactions")]
    + [("Reprocessing", c) for c in ("Athanor (Ore)", "Athanor (Moon)", "Athanor (Ice)", "Tatara (All)")]
    + [("Reactions", "Athanor"), ("Reactions", "Tatara")]
    + [("Advanced Component", c) for c in ("Raitaru", "Azbel", "Sotiyo")]
    + [("Subcapital Production", c) for c in (
        "T1 Small Ship", "T1 Med Ship", "T1 Large Ship", "T2 Small Ship", "T2 Med Ship", "T2 Large Ship",
        "Freighter", "Jump Freighter", "Structure", "Fighter / Drone", "Equipment", "Ammo")]
    + [("Capital Component", c) for c in ("Raitaru", "Azbel", "Sotiyo")]
    + [("Capital Production", c) for c in ("Azbel", "Sotiyo")]
    + [("Supercapital", "Sotiyo")]
)


def _line(location, cells):
    return [location if name == "Location" else cells.get((group, name), "") for group, name in COLUMNS]


def _block(rows):
    groups = []
    previous = None
    for group, _ in COLUMNS:
        groups.append(group if group != previous else "")
        previous = group
    return [groups, [name for _, name in COLUMNS], *rows]


def _csv(*blocks):
    out = io.StringIO()
    writer = csv.writer(out)
    for row in (["Alliance rig overview", "notes"], []):
        writer.writerow(row)
    for block in blocks:
        for row in block:
            writer.writerow(row)
        writer.writerow([])
    return out.getvalue()


RESEARCH_BLOCK = [
    ["", "Research", "", "", ""],
    ["Location", "Copying", "Invention", "ME", "TE"],
    ["Fantasyia", "T2", "T2", "T1", "T1"],
    ["Elsewhere", "T2", "N", "T1", "T1"],
]


def _fantasy_rows():
    return [
        _line("Fantasyia", {
            ("Reactions", "Athanor"): "T2 (All)", ("Reactions", "Tatara"): "T2 (Comp)",
            ("Advanced Component", "Raitaru"): "T1", ("Advanced Component", "Azbel"): "→",
            ("Advanced Component", "Sotiyo"): "T2",
            ("Subcapital Production", "T1 Small Ship"): "T1", ("Subcapital Production", "T1 Med Ship"): "N",
            ("Subcapital Production", "T1 Large Ship"): "F", ("Subcapital Production", "T2 Small Ship"): "T2",
            ("Subcapital Production", "T2 Med Ship"): "←", ("Subcapital Production", "T2 Large Ship"): "→",
            ("Subcapital Production", "Equipment"): "bogus",
            ("Subcapital Production", "Fighter / Drone"): "t2",
            ("Capital Component", "Raitaru"): "T2", ("Capital Component", "Azbel"): "←",
            ("Capital Component", "Sotiyo"): "←",
            ("Capital Production", "Azbel"): "→", ("Capital Production", "Sotiyo"): "←",
            ("Supercapital", "Sotiyo"): "←",
        }),
        _line("Nowhereville", {("Advanced Component", "Raitaru"): "T2", ("Advanced Component", "Azbel"): "←",
                               ("Advanced Component", "Sotiyo"): "←",
                               ("Reactions", "Athanor"): "N"}),
        _line("Reactor Hill", {("Reactions", "Athanor"): "T1", ("Reactions", "Tatara"): "→"}),
        _line("Arrowtown", {("Advanced Component", "Raitaru"): "→", ("Advanced Component", "Azbel"): "→",
                            ("Advanced Component", "Sotiyo"): "T1"}),
    ]


RAITARU = "Raitaru (M Engineering Complex)"
AZBEL = "Azbel (L Engineering Complex)"
SOTIYO = "Sotiyo (XL Engineering Complex)"
ATHANOR = "Athanor (M Refinery)"
TATARA = "Tatara (L Refinery)"


def _parsed(rows=None):
    return rig_sheet.parse_csv(_csv(_block(rows or _fantasy_rows())))


def _cells(state, name):
    return rig_sheet.system_cells(state["systems"], name)


# ------------------------------------------------------------------- parser
def test_codes_and_qualifiers():
    cells = _cells(_parsed(), "Fantasyia")
    # T2 (All) applies to every reaction; T2 (Comp) names one reaction kind, which a category is not.
    assert rig_sheet.lookup(cells, "Reactions", False, ATHANOR) == T2
    assert rig_sheet.lookup(cells, "Reactions", False, TATARA) is None
    assert rig_sheet.lookup(cells, "Small Ships", False, None) == T1
    assert rig_sheet.lookup(cells, "Medium Ships", False, None) == NO          # N
    assert rig_sheet.lookup(cells, "Large Ships", False, None) is None         # F = planned
    assert rig_sheet.lookup(cells, "Equipment", False, None) is None           # unknown code
    assert rig_sheet.lookup(cells, "Ammunition", False, None) is None          # empty
    assert rig_sheet.lookup(cells, "Drones & Fighters", False, None) == T2     # lower case code


def test_ship_sizes_use_the_tech_column():
    cells = _cells(_parsed(), "Fantasyia")
    assert rig_sheet.lookup(cells, "Small Ships", True, None) == T2
    assert rig_sheet.lookup(cells, "Small Ships", False, None) == T1
    # T2 Med points left at T2 Small.
    assert rig_sheet.lookup(cells, "Medium Ships", True, None) == T2


def test_arrow_chains_in_both_directions():
    state = _parsed()
    fantasy = _cells(state, "Fantasyia")
    # Capital Component: Sotiyo <- Azbel <- Raitaru (T2).
    assert rig_sheet.lookup(fantasy, "Capital Components", False, SOTIYO) == T2
    assert rig_sheet.lookup(fantasy, "Capital Components", False, AZBEL) == T2
    # Advanced Component: Azbel -> Sotiyo (T2).
    assert rig_sheet.lookup(fantasy, "Advanced Components", False, AZBEL) == T2
    assert rig_sheet.lookup(fantasy, "Advanced Components", False, RAITARU) == T1
    nowhere = _cells(state, "Nowhereville")
    assert [rig_sheet.lookup(nowhere, "Advanced Components", False, t) for t in (RAITARU, AZBEL, SOTIYO)] == [T2] * 3
    # Raitaru -> Azbel -> Sotiyo (T1): a chain of two arrows to the right.
    arrow = _cells(state, "Arrowtown")
    assert [rig_sheet.lookup(arrow, "Advanced Components", False, t) for t in (RAITARU, AZBEL, SOTIYO)] == [T1] * 3


def test_arrow_into_the_group_end_or_a_loop_gives_no_value():
    state = _parsed()
    fantasy = _cells(state, "Fantasyia")
    # T2 Large Ship points right at Freighter (empty, same group): no value.
    assert rig_sheet.lookup(fantasy, "Large Ships", True, None) is None
    # Capital Production Azbel -> / Sotiyo <- is a loop.
    assert rig_sheet.lookup(fantasy, "Capital Ship", False, AZBEL) is None
    assert rig_sheet.lookup(fantasy, "Capital Ship", False, SOTIYO) is None
    # Supercapital's only column points left: the neighbour belongs to another group.
    assert rig_sheet.lookup(fantasy, "Super Capital Ship", False, SOTIYO) is None
    # Reactions Tatara -> leaves the Reactions group.
    assert rig_sheet.lookup(_cells(state, "Reactor Hill"), "Reactions", False, TATARA) is None


def test_structure_type_picks_the_column_and_unknown_type_gives_no_value():
    cells = _cells(_parsed(), "Fantasyia")
    assert rig_sheet.lookup(cells, "Advanced Components", False, SOTIYO) == T2
    assert rig_sheet.lookup(cells, "Advanced Components", False, "Citadel (no bonuses)") is None
    assert rig_sheet.lookup(cells, "Advanced Components", False, None) is None
    assert rig_sheet.lookup(_cells(_parsed(), "Reactor Hill"), "Reactions", False, ATHANOR) == T1


def test_n_is_no_rig_and_unlisted_system_has_no_cells():
    state = _parsed()
    assert rig_sheet.lookup(_cells(state, "Nowhereville"), "Reactions", False, ATHANOR) == NO
    assert _cells(state, "Atlantis") is None
    assert _cells(state, "  fantasyia ") is not None  # names match case-insensitively, trimmed


def test_a_system_in_both_production_blocks_keeps_the_first_row():
    second = [_line("fantasyia", {("Advanced Component", "Raitaru"): "N"}), _line("Newtown", {})]
    state = rig_sheet.parse_csv(_csv(_block(_fantasy_rows()), _block(second)))
    assert state["blocks"] == 2
    assert state["duplicates"] == ["fantasyia"]
    assert rig_sheet.lookup(_cells(state, "Fantasyia"), "Advanced Components", False, RAITARU) == T1
    assert _cells(state, "Newtown") is not None


def test_research_blocks_are_ignored():
    state = rig_sheet.parse_csv(_csv(_block(_fantasy_rows()), RESEARCH_BLOCK))
    assert state["blocks"] == 1
    assert "elsewhere" not in state["systems"]


def test_the_next_blocks_group_row_is_not_read_as_a_system():
    state = rig_sheet.parse_csv(_csv(_block(_fantasy_rows()), _block([_line("Newtown", {})])))
    assert set(state["systems"]) == {"fantasyia", "nowhereville", "reactor hill", "arrowtown", "newtown"}


def test_a_sheet_without_a_column_row_is_rejected():
    with pytest.raises(rig_sheet.RigSheetError):
        rig_sheet.parse_csv("a,b,c\n1,2,3\n")
    with pytest.raises(rig_sheet.RigSheetError):
        rig_sheet.parse_csv(_csv(RESEARCH_BLOCK))  # a column row, but no known rig columns
    with pytest.raises(rig_sheet.RigSheetError):
        rig_sheet.parse_csv(_csv(_block([])))      # no system rows


def test_short_and_ragged_rows_do_not_break_the_parser():
    rows = [["Shorty", "x"], _line("Fantasyia", {("Advanced Component", "Raitaru"): "T1"})]
    state = _parsed(rows)
    assert _cells(state, "Shorty") == {}
    assert rig_sheet.lookup(_cells(state, "Fantasyia"), "Advanced Components", False, RAITARU) == T1


def test_mapping_table_lists_every_mapped_category():
    assert set(rig_sheet.mapped_categories()) == {
        "Reactions", "Advanced Components", "Capital Components", "Capital Ship", "Super Capital Ship",
        "Small Ships", "Medium Ships", "Large Ships", "Drones & Fighters", "Equipment", "Ammunition"}


# -------------------------------------------------------------------- fetch
class _Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self.headers, self._body, self.closed = status, headers or {}, body, False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


@pytest.mark.parametrize("url", [
    "https://evil.example.com/spreadsheets/d/FAKESHEETID0000000001/edit",
    "https://docs.google.com.evil.example.com/spreadsheets/d/FAKESHEETID0000000001/edit",
    "http://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/edit",
    "https://user@docs.google.com/spreadsheets/d/FAKESHEETID0000000001/edit",
    "https://docs.google.com/document/d/FAKESHEETID0000000001/edit",
    "https://docs.google.com/spreadsheets/d/short/edit",
    "https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/edit#gid=abc",
    "ftp://docs.google.com/x", "", "not a url",
])
def test_foreign_or_malformed_links_are_rejected(url, monkeypatch):
    monkeypatch.setattr(rig_sheet, "_http_get", lambda *a, **k: pytest.fail("network used"))
    with pytest.raises(rig_sheet.RigSheetError):
        rig_sheet.fetch_csv(url)
    assert not rig_sheet.is_valid_url(url)


def test_export_address_is_built_from_id_and_gid():
    assert rig_sheet.export_url(FAKE_URL) == (
        "https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/export?format=csv&gid=42")
    assert rig_sheet.export_url("https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/edit?gid=7").endswith("gid=7")
    assert rig_sheet.export_url("https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001").endswith("gid=0")


def test_fetch_follows_a_googleusercontent_redirect_only(monkeypatch):
    seen = []
    responses = iter([
        _Response(307, headers={"Location": "https://doc-0g-abc.googleusercontent.com/export/x"}),
        _Response(200, body=b"a,b\n1,2\n"),
    ])
    monkeypatch.setattr(rig_sheet, "_http_get", lambda url, timeout: (seen.append((url, timeout)), next(responses))[1])
    assert rig_sheet.fetch_csv(FAKE_URL) == "a,b\n1,2\n"
    assert seen[0][0].startswith("https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/export")
    assert seen[1][0].startswith("https://doc-0g-abc.googleusercontent.com/")
    assert all(0 < timeout <= 10 for _, timeout in seen)


@pytest.mark.parametrize("target", [
    "https://accounts.google.com/ServiceLogin", "https://evil.example.com/x",
    "https://googleusercontent.com.evil.example.com/x", "http://doc.googleusercontent.com/x",
])
def test_fetch_refuses_other_redirects(monkeypatch, target):
    monkeypatch.setattr(rig_sheet, "_http_get", lambda url, timeout: _Response(302, headers={"Location": target}))
    with pytest.raises(rig_sheet.RigSheetError) as caught:
        rig_sheet.fetch_csv(FAKE_URL)
    assert "FAKESHEETID" not in str(caught.value) and target not in str(caught.value)


def test_http_error_message_has_no_server_text(monkeypatch):
    monkeypatch.setattr(rig_sheet, "_http_get", lambda url, timeout: _Response(500, body=b"secret body FAKESHEETID"))
    with pytest.raises(rig_sheet.RigSheetError) as caught:
        rig_sheet.fetch_csv(FAKE_URL)
    assert str(caught.value) == "The rig sheet could not be fetched (HTTP 500)."


def test_size_cap_and_network_error(monkeypatch):
    big = _Response(200, body=b"x" * (rig_sheet.MAX_BYTES + 1))
    monkeypatch.setattr(rig_sheet, "_http_get", lambda url, timeout: big)
    with pytest.raises(rig_sheet.RigSheetError, match="1 MiB"):
        rig_sheet.fetch_csv(FAKE_URL)
    assert big.closed

    def boom(url, timeout):
        raise requests.ConnectionError("dns fail for FAKESHEETID")

    monkeypatch.setattr(rig_sheet, "_http_get", boom)
    with pytest.raises(rig_sheet.RigSheetError) as caught:
        rig_sheet.fetch_csv(FAKE_URL)
    assert "FAKESHEETID" not in str(caught.value)


def test_redirect_loop_is_cut_off(monkeypatch):
    monkeypatch.setattr(rig_sheet, "_http_get", lambda url, timeout: _Response(
        302, headers={"Location": "https://docs.google.com/spreadsheets/d/FAKESHEETID0000000001/export"}))
    with pytest.raises(rig_sheet.RigSheetError, match="too often"):
        rig_sheet.fetch_csv(FAKE_URL)


def test_settings_validation_rejects_a_foreign_link():
    validate_production_overrides({"rig_sheet_url": FAKE_URL})
    validate_production_overrides({"rig_sheet_url": ""})
    with pytest.raises(ConfigError):
        validate_production_overrides({"rig_sheet_url": "https://evil.example.com/spreadsheets/d/FAKESHEETID0000000001"})


# ------------------------------------------------------------- resolution
@pytest.fixture
def facilities(monkeypatch):
    data: dict = {}
    monkeypatch.setattr(engine, "_load_category_facilities", lambda: data)
    monkeypatch.setattr(engine, "_security_multiplier_for", lambda profile, cfg: 1.0)
    return data


def _facility(cells, name=None, **saved):
    return {"structure_type": saved.get("structure_type"), "rig_tier_basic": saved.get("rig_tier_basic"),
            "rig_tier_advanced": saved.get("rig_tier_advanced"), "location_id": 1, "structure_name": name,
            "system_id": 30000709, "security": -0.5, "sheet_cells": cells}


def _cfg(url=FAKE_URL):
    return ProductionConfig(manufacturing_structure_type=RAITARU, manufacturing_rig_tier=T2,
                            component_structure_type=RAITARU, component_rig_tier="No Rig",
                            reaction_structure_type=ATHANOR, rig_sheet_url=url)


def _resolve(category="Advanced Components", activity="Tech I", profile="component", cfg=None):
    return engine.facility_resolution(category, activity, profile, cfg or _cfg())


def test_order_saved_then_sheet_then_name_then_global(facilities):
    cells = _cells(_parsed(), "Fantasyia")   # Advanced Components at a Raitaru: T1
    facilities["Advanced Components"] = _facility(cells, name="XYZ-1 - T2 Adv Comp")
    resolved = _resolve()
    assert (resolved["rig_tier"], resolved["rig_source"]) == (T1, "sheet")
    assert (resolved["te_rig_tier"], resolved["te_rig_source"]) == (T1, "same as ME")

    facilities["Advanced Components"] = _facility(cells, name="XYZ-1 - T2 Adv Comp", rig_tier_basic="T2-Rig")
    assert (_resolve()["rig_tier"], _resolve()["rig_source"]) == (T2, "set")

    # No sheet value for the cell: the name, then the global profile.
    facilities["Advanced Components"] = _facility({}, name="XYZ-1 - T2 Adv Comp")
    assert (_resolve()["rig_tier"], _resolve()["rig_source"]) == (T2, "name")
    facilities["Advanced Components"] = _facility({}, name="XYZ-1 - Workshop")
    assert (_resolve()["rig_tier"], _resolve()["rig_source"]) == ("No Rig", "global")


def test_n_in_the_sheet_beats_the_name(facilities):
    cells = _cells(_parsed(), "Nowhereville")
    facilities["Reactions"] = _facility(cells, name="ABC-1 - T2 Reactions")
    resolved = _resolve("Reactions", profile="reaction")
    assert (resolved["rig_tier"], resolved["rig_source"]) == ("No Rig", "sheet")


def test_sheet_is_inert_without_a_link_or_without_the_system(facilities):
    cells = _cells(_parsed(), "Fantasyia")
    facilities["Advanced Components"] = _facility(cells, name="XYZ-1 - T2 Adv Comp")
    assert _resolve(cfg=_cfg(url=""))["rig_source"] == "name"
    facilities["Advanced Components"] = _facility(None, name="XYZ-1 - T2 Adv Comp")   # system not in the sheet
    assert _resolve()["rig_source"] == "name"


def test_sheet_reads_the_structure_type_the_category_uses(facilities):
    cells = _cells(_parsed(), "Fantasyia")
    facilities["Advanced Components"] = _facility(cells, structure_type=SOTIYO)
    assert (_resolve()["rig_tier"], _resolve()["rig_source"]) == (T2, "sheet")
    facilities["Advanced Components"] = _facility(cells, structure_type="Citadel (no bonuses)")
    assert _resolve()["rig_source"] == "global"


def test_ship_category_uses_the_basic_or_advanced_sheet_column(facilities):
    cells = _cells(_parsed(), "Fantasyia")
    facilities["Small Ships"] = _facility(cells)
    assert _resolve("Small Ships", "Tech I", "manufacturing")["rig_tier"] == T1
    assert _resolve("Small Ships", "Tech II", "manufacturing")["rig_tier"] == T2
    assert _resolve("Small Ships", "Tech II", "manufacturing")["rig_source"] == "sheet"


def test_te_stays_a_separate_choice_with_a_sheet_me_tier(facilities):
    cells = _cells(_parsed(), "Fantasyia")
    facilities["Advanced Components"] = _facility(cells)
    facilities["Advanced Components"]["te_rig_tier_basic"] = "No Rig"
    resolved = _resolve()
    assert (resolved["rig_tier"], resolved["te_rig_tier"], resolved["te_rig_source"]) == (T1, "No Rig", "set")


# --------------------------------------------------------- storage / actions
pytestmark_pg = pg_helpers.postgres_required()


@pytest.fixture
def stored(tenant, _apply_job_category_cost_index_overrides_schema, monkeypatch):
    pg_helpers.wipe_tables("production_rig_sheet")
    engine.invalidate_facility_cache(all_tenants=True)
    cfg = ProductionConfig(rig_sheet_url=FAKE_URL)
    monkeypatch.setattr(engine, "PRODUCTION_CONFIG", cfg)
    monkeypatch.setattr(storage, "load_category_facilities", lambda: {})
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1})
    monkeypatch.setattr(storage, "load_category_system_ids", lambda: {"Advanced Components": 30000709})
    monkeypatch.setattr(storage, "get_location_names", lambda ids: {1: "XYZ-1 - Workshop"})
    monkeypatch.setattr(storage, "get_system_name", lambda system_id: "Fantasyia")
    monkeypatch.setattr(storage, "get_system_security", lambda system_id: -0.5)
    monkeypatch.setattr(engine.facility_tax, "observed_rates", lambda: {})
    yield cfg
    engine.invalidate_facility_cache(all_tenants=True)


def _serve(monkeypatch, text):
    def get(url, timeout):
        if isinstance(text, Exception):
            raise text
        return _Response(200, body=text.encode())
    monkeypatch.setattr(rig_sheet, "_http_get", get)


@pytestmark_pg
def test_reload_stores_the_state_and_the_loader_serves_it(stored, monkeypatch):
    _serve(monkeypatch, _csv(_block(_fantasy_rows())))
    status = actions.do_reload_rig_sheet(stored)
    assert status["ok"] and status["system_count"] == 4 and status["error"] is None
    assert status["fetched_at"] and status["age_hours"] < 0.1
    loaded = engine._load_category_facilities()["Advanced Components"]
    assert rig_sheet.lookup(loaded["sheet_cells"], "Advanced Components", False, RAITARU) == T1


@pytestmark_pg
def test_failed_fetch_keeps_the_old_state_and_shows_the_error(stored, monkeypatch):
    _serve(monkeypatch, _csv(_block(_fantasy_rows())))
    actions.do_reload_rig_sheet(stored)
    for failure in (requests.ConnectionError("down"), "not,a,sheet\n1,2,3\n"):
        _serve(monkeypatch, failure)
        status = actions.do_reload_rig_sheet(stored)
        assert not status["ok"] and status["error"] and "FAKESHEETID" not in status["error"]
        assert status["system_count"] == 4 and status["fetched_at"]
    engine.invalidate_facility_cache()
    assert engine._load_category_facilities()["Advanced Components"]["sheet_cells"]
    _serve(monkeypatch, _csv(_block(_fantasy_rows())))
    assert actions.do_reload_rig_sheet(stored)["error"] is None


@pytestmark_pg
def test_a_changed_link_never_reads_the_old_sheet(stored, monkeypatch):
    _serve(monkeypatch, _csv(_block(_fantasy_rows())))
    actions.do_reload_rig_sheet(stored)
    stored.rig_sheet_url = "https://docs.google.com/spreadsheets/d/OTHERSHEETID000000002/edit#gid=0"
    engine.invalidate_facility_cache()
    assert engine._load_category_facilities()["Advanced Components"]["sheet_cells"] is None
    assert actions.do_get_rig_sheet_status(stored)["fetched_at"] is None
    _serve(monkeypatch, requests.ConnectionError("down"))
    status = actions.do_reload_rig_sheet(stored)       # the failure drops rows of the other sheet
    assert status["system_count"] == 0 and status["error"]


@pytestmark_pg
def test_without_a_link_there_is_nothing_to_reload_and_no_sheet_cells(stored):
    stored.rig_sheet_url = ""
    with pytest.raises(actions.ActionError):
        actions.do_reload_rig_sheet(stored)
    engine.invalidate_facility_cache()
    assert engine._load_category_facilities()["Advanced Components"]["sheet_cells"] is None
    assert actions.do_get_rig_sheet_status(stored)["configured"] is False


@pytestmark_pg
def test_saving_a_new_link_fetches_once_and_a_failure_does_not_fail_the_save(stored, monkeypatch):
    stored.rig_sheet_url = ""
    saved = {}

    def fake_save(scope, updates, cfg, cfg_type):
        saved.update(updates)
        for key, value in updates.items():
            setattr(cfg, key, value)

    monkeypatch.setattr(actions, "save_tenant_config_overrides", fake_save)
    calls = []

    def failing_fetch(url):
        calls.append(url)
        raise rig_sheet.RigSheetError("The rig sheet could not be reached (network error).")

    monkeypatch.setattr(rig_sheet, "fetch_csv", failing_fetch)
    actions.do_update_settings({"rig_sheet_url": FAKE_URL}, stored)
    assert len(calls) == 1 and saved["rig_sheet_url"] == FAKE_URL
    assert actions.do_get_rig_sheet_status(stored)["error"]
    actions.do_update_settings({"rig_sheet_url": FAKE_URL}, stored)    # unchanged link: no new fetch
    assert len(calls) == 1
    actions.do_update_settings({"rig_sheet_url": ""}, stored)
    assert storage.load_rig_sheet() is None


@pytestmark_pg
def test_category_list_carries_the_sheet_value_and_status(stored, monkeypatch):
    _serve(monkeypatch, _csv(_block(_fantasy_rows())))
    actions.do_reload_rig_sheet(stored)
    stored.component_structure_type = RAITARU
    result = actions.do_list_category_facilities(stored)
    row = next(r for r in result["categories"] if r["category"] == "Advanced Components")
    assert row["from_sheet"]["rig_tier_basic"] == T1
    assert row["effective"]["rig_source_basic"] == "sheet"
    assert result["rig_sheet"]["system_count"] == 4

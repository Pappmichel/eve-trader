"""docs/MANUAL_TRACKING_PLAN.md phase 8 - admin bulk structure-name
resolution. Unit-level (monkeypatched storage/ESI, no Postgres), same shape
as tests/test_resolve_structure_name.py's own tier tests."""
import pytest

from eve_trader import admin, storage
from eve_trader.actions import ActionError
from eve_trader.esi_client import ESIClient
from eve_trader.production import esi_sync
from eve_trader.production.config import PRODUCTION_CONFIG


@pytest.fixture(autouse=True)
def _stub_candidates(monkeypatch):
    monkeypatch.setattr(storage, "candidate_structure_location_ids", lambda: set())


def test_structure_resolve_candidates_combines_storage_ids_and_config_locations(monkeypatch):
    monkeypatch.setattr(storage, "candidate_structure_location_ids",
                         lambda: {1035466617946, 12345})  # 12345 is below STRUCTURE_ID_MIN - an NPC station
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_location_id", 1035466617947)
    monkeypatch.setattr(PRODUCTION_CONFIG, "distribution_source_location_id", 1035466617948)
    monkeypatch.setattr(PRODUCTION_CONFIG, "invention_location_id", None)

    result = admin._structure_resolve_candidates()

    assert result == {1035466617946, 1035466617947, 1035466617948}


def test_do_resolve_structure_names_skips_already_globally_cached_when_not_forced(monkeypatch):
    monkeypatch.setattr(admin, "_structure_resolve_candidates", lambda: {1035466617946, 1035466617947})
    monkeypatch.setattr(storage, "get_global_structure_names",
                         lambda location_ids: {1035466617946: ("Known", 30000142)})
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                         lambda capability_key: [("esi:1", 1, "Alice")])
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_sync, "corp_roles_for_characters", lambda client, characters: {99: "esi"})
    resolved_ids = {}
    monkeypatch.setattr(esi_sync, "resolve_structure_ids", lambda client, to_resolve, corp_roles, get_characters:
                         resolved_ids.update({loc: ("Newly Resolved", 30000144) for loc in to_resolve}) or resolved_ids)
    captured = {}
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: captured.setdefault("cached", []).append(loc_id))
    monkeypatch.setattr(storage, "upsert_global_structure_name",
                         lambda location_id, name, solar_system_id=None: captured.setdefault("global", []).append(location_id))

    result = admin.do_resolve_structure_names(force=False)

    assert result == {"candidates": 2, "resolved": 1}
    assert captured["cached"] == [1035466617947]
    assert captured["global"] == [1035466617947]


def test_do_resolve_structure_names_force_resolves_everything_even_if_globally_cached(monkeypatch):
    monkeypatch.setattr(admin, "_structure_resolve_candidates", lambda: {1035466617946})
    monkeypatch.setattr(storage, "get_global_structure_names",
                         lambda location_ids: pytest.fail("must not consult the global cache when force=True"))
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                         lambda capability_key: [("esi:1", 1, "Alice")])
    monkeypatch.setattr(esi_sync, "corp_roles_for_characters", lambda client, characters: {})
    monkeypatch.setattr(esi_sync, "resolve_structure_ids",
                         lambda client, to_resolve, corp_roles, get_characters:
                         {1035466617946: ("Re-resolved", 30000142)})
    monkeypatch.setattr(storage, "set_cached_structure_name", lambda *a, **kw: None)
    monkeypatch.setattr(storage, "upsert_global_structure_name", lambda *a, **kw: None)

    result = admin.do_resolve_structure_names(force=True)

    assert result == {"candidates": 1, "resolved": 1}


def test_do_resolve_structure_names_no_candidates_left_is_a_noop(monkeypatch):
    monkeypatch.setattr(admin, "_structure_resolve_candidates", lambda: {1035466617946})
    monkeypatch.setattr(storage, "get_global_structure_names",
                         lambda location_ids: {1035466617946: ("Known", 30000142)})
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                         lambda capability_key: pytest.fail("must not look up characters with nothing to resolve"))

    result = admin.do_resolve_structure_names(force=False)

    assert result == {"candidates": 1, "resolved": 0}


def test_do_resolve_structure_names_raises_without_any_capable_character(monkeypatch):
    monkeypatch.setattr(admin, "_structure_resolve_candidates", lambda: {1035466617946})
    monkeypatch.setattr(storage, "get_global_structure_names", lambda location_ids: {})
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda capability_key: [])

    with pytest.raises(ActionError, match="Structure name resolution"):
        admin.do_resolve_structure_names(force=False)


def test_do_resolve_structure_names_reports_progress(monkeypatch):
    monkeypatch.setattr(admin, "_structure_resolve_candidates", lambda: {1035466617946})
    monkeypatch.setattr(storage, "get_global_structure_names", lambda location_ids: {})
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                         lambda capability_key: [("esi:1", 1, "Alice")])
    monkeypatch.setattr(esi_sync, "corp_roles_for_characters", lambda client, characters: {})
    monkeypatch.setattr(esi_sync, "resolve_structure_ids",
                         lambda client, to_resolve, corp_roles, get_characters:
                         {1035466617946: ("Resolved", 30000142)})
    monkeypatch.setattr(storage, "set_cached_structure_name", lambda *a, **kw: None)
    monkeypatch.setattr(storage, "upsert_global_structure_name", lambda *a, **kw: None)
    messages = []

    admin.do_resolve_structure_names(force=False, progress_callback=messages.append)

    assert len(messages) == 2
    assert "1" in messages[0]["message"]
    assert "1" in messages[1]["message"]


def test_do_start_structure_name_resolve_delegates_to_pipeline_runner(monkeypatch):
    from eve_trader import pipeline_runner
    captured = {}
    monkeypatch.setattr(pipeline_runner, "start_structure_name_resolve",
                         lambda force=False: captured.update(force=force) or {"run_id": "x"})

    result = admin.do_start_structure_name_resolve(force=True)

    assert captured == {"force": True}
    assert result == {"run_id": "x"}


def test_do_structure_resolve_status_delegates_to_pipeline_runner(monkeypatch):
    from eve_trader import pipeline_runner
    monkeypatch.setattr(pipeline_runner, "job_status",
                         lambda tool, job_name=None: {"tool": tool, "job_name": job_name})

    result = admin.do_structure_resolve_status()

    assert result == {"tool": pipeline_runner.TOOL_ADMIN, "job_name": pipeline_runner.JOB_STRUCTURE_RESOLVE}

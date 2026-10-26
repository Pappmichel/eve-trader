"""docs/MANUAL_TRACKING_PLAN.md phase 8 - admin bulk structure-name
resolution. Unit-level (monkeypatched storage/ESI, no Postgres), same shape
as tests/test_resolve_structure_name.py's own tier tests."""
import pytest

from eve_trader import admin, storage
from eve_trader.actions import ActionError
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


def _fake_resolver(monkeypatch, summary, captured=None):
    from eve_trader.locations import resolver

    def fake(**kw):
        if captured is not None:
            captured.update(kw)
        return dict(summary)
    monkeypatch.setattr(resolver, "resolve_pending", fake)


def test_do_resolve_structure_names_runs_the_central_resolver(monkeypatch):
    captured = {}
    _fake_resolver(monkeypatch, {"due": 3, "resolved": 1, "from_global": 1, "forbidden": 1, "calls": 4}, captured)

    result = admin.do_resolve_structure_names(force=False)

    assert result["candidates"] == 3 and result["resolved"] == 2 and result["forbidden"] == 1
    assert captured["force"] is False and captured["budget"] == admin.ADMIN_RESOLVE_BUDGET


def test_do_resolve_structure_names_passes_force_through(monkeypatch):
    captured = {}
    _fake_resolver(monkeypatch, {"due": 1, "resolved": 1}, captured)

    admin.do_resolve_structure_names(force=True)

    assert captured["force"] is True


def test_do_resolve_structure_names_nothing_due_is_a_noop(monkeypatch):
    _fake_resolver(monkeypatch, {"due": 0, "resolved": 0, "from_global": 0})

    assert admin.do_resolve_structure_names(force=False)["resolved"] == 0


def test_do_resolve_structure_names_raises_without_any_capable_character(monkeypatch):
    _fake_resolver(monkeypatch, {"due": 2, "skipped": "no character has Structure name resolution ticked"})

    with pytest.raises(ActionError, match="Structure name resolution"):
        admin.do_resolve_structure_names(force=False)


def test_do_resolve_structure_names_reports_progress(monkeypatch):
    _fake_resolver(monkeypatch, {"due": 1, "resolved": 1, "from_global": 0})
    messages = []

    admin.do_resolve_structure_names(force=False, progress_callback=messages.append)

    assert len(messages) == 2
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

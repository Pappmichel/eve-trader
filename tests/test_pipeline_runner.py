"""Tests for the background Trading job runner.

Lock behaviour and the with_current_tenant wrap are unit-tested with
storage/threading monkeypatches (no Postgres). Persistence + the unique
running-job index need a real DB.
"""
import pytest

from eve_trader import pipeline_runner, storage
from eve_trader.actions import ConflictError
from eve_trader.pipeline_runner import JOB_REFRESH_AND_PRUNE, JOB_REFRESH_SHORTLIST

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _noop_fail_stale(job_name=None, stale_after_seconds=7200):
    return 0


def test_second_start_rejected_when_a_run_is_already_running(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", _noop_fail_stale)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: {
        "run_id": "already", "status": "running", "job_name": JOB_REFRESH_AND_PRUNE,
    })
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started.append("start")

    monkeypatch.setattr(pipeline_runner.threading, "Thread", FakeThread)

    with pytest.raises(ConflictError, match="already running"):
        pipeline_runner.start_refresh_and_prune()
    assert started == []


def test_second_start_of_a_different_job_is_rejected(monkeypatch):
    """Refresh Shortlist / Search+Add / Pipeline share one per-tenant lock."""
    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", _noop_fail_stale)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: {
        "run_id": "already", "status": "running", "job_name": JOB_REFRESH_AND_PRUNE,
    })
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started.append("start")

    monkeypatch.setattr(pipeline_runner.threading, "Thread", FakeThread)

    with pytest.raises(ConflictError, match="Search \\+ Add \\+ Clean Up is already running"):
        pipeline_runner.start_refresh_shortlist()
    with pytest.raises(ConflictError, match="already running"):
        pipeline_runner.start_pipeline()
    assert started == []


def test_start_wraps_the_worker_with_current_tenant(monkeypatch):
    wrapped = []
    original = storage.with_current_tenant

    def spy(fn):
        wrapped.append(fn)
        return original(fn)

    monkeypatch.setattr(storage, "with_current_tenant", spy)
    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", _noop_fail_stale)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: None)
    monkeypatch.setattr(storage, "start_pipeline_run", lambda job, tool="trading": "run-1")

    captured = {}

    class FakeThread:
        def __init__(self, target=None, daemon=None, name=None):
            captured["target"] = target
            captured["daemon"] = daemon

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(pipeline_runner.threading, "Thread", FakeThread)

    result = pipeline_runner.start_refresh_and_prune(safe=False)

    assert result == {"run_id": "run-1", "status": "running", "job_name": JOB_REFRESH_AND_PRUNE, "tool": "trading"}
    assert wrapped, "storage.with_current_tenant must wrap the worker target"
    assert captured.get("started") is True
    assert captured.get("daemon") is True


def test_start_refresh_shortlist_returns_running(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", _noop_fail_stale)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: None)
    monkeypatch.setattr(storage, "start_pipeline_run", lambda job, tool="trading": "run-sl")
    monkeypatch.setattr(storage, "with_current_tenant", lambda fn: fn)

    class FakeThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(pipeline_runner.threading, "Thread", FakeThread)

    result = pipeline_runner.start_refresh_shortlist()
    assert result == {"run_id": "run-sl", "status": "running", "job_name": JOB_REFRESH_SHORTLIST, "tool": "trading"}


def test_unique_violation_on_insert_maps_to_conflict(monkeypatch):
    from psycopg.errors import UniqueViolation

    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", _noop_fail_stale)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: None)

    def _raise(job, tool="trading"):
        raise UniqueViolation("pipeline_runs_one_running_per_tenant")
    monkeypatch.setattr(storage, "start_pipeline_run", _raise)

    with pytest.raises(ConflictError, match="already running"):
        pipeline_runner.start_refresh_and_prune()


def test_runner_marks_succeeded_when_cleanup_reports_skips(monkeypatch):
    """A partial cleanup (some ESI batches skipped) is still a succeeded job
    - the skip counts live in result, not in status=failed."""
    from contextlib import contextmanager

    from eve_trader import actions

    monkeypatch.setattr(
        actions, "do_refresh_and_prune_candidates",
        lambda safe=True, progress_callback=None: {
            "cleanup_items_skipped": 12,
            "cleanup_skipped_item_ids": list(range(12)),
        },
    )
    finished = []
    monkeypatch.setattr(
        storage, "finish_pipeline_run",
        lambda run_id, status, result=None, error=None: finished.append((status, result, error)),
    )
    monkeypatch.setattr(storage, "update_pipeline_run_progress", lambda *a, **k: None)

    @contextmanager
    def _enter(tenant_id):
        yield

    monkeypatch.setattr(pipeline_runner.tenant_scope, "enter_tenant", _enter)
    pipeline_runner._run_refresh_and_prune(_TENANT_ID, "run-1", True)

    assert finished == [(
        "succeeded",
        {"cleanup_items_skipped": 12, "cleanup_skipped_item_ids": list(range(12))},
        None,
    )]


def test_refresh_shortlist_worker_marks_succeeded(monkeypatch):
    from contextlib import contextmanager

    from eve_trader import actions

    monkeypatch.setattr(
        actions, "do_refresh_shortlist",
        lambda progress_callback=None: {"priced_via_fallback": False},
    )
    finished = []
    monkeypatch.setattr(
        storage, "finish_pipeline_run",
        lambda run_id, status, result=None, error=None: finished.append((status, result)),
    )
    monkeypatch.setattr(storage, "update_pipeline_run_progress", lambda *a, **k: None)

    @contextmanager
    def _enter(tenant_id):
        yield

    monkeypatch.setattr(pipeline_runner.tenant_scope, "enter_tenant", _enter)
    pipeline_runner._run_refresh_shortlist(_TENANT_ID, "run-sl")

    assert finished == [("succeeded", {"priced_via_fallback": False})]


def test_trading_job_status_prefers_running_over_latest(monkeypatch):
    from eve_trader import actions

    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: {
        "run_id": "r1", "status": "running", "job_name": JOB_REFRESH_SHORTLIST,
    })
    monkeypatch.setattr(storage, "get_latest_pipeline_run", lambda job_name=None, tool=None: {
        "run_id": "old", "status": "succeeded", "job_name": JOB_REFRESH_AND_PRUNE, "tool": "trading",
    })
    assert actions.do_trading_job_status()["run_id"] == "r1"
    assert actions.do_refresh_and_prune_status()["run_id"] == "r1"


def test_start_refresh_shortlist_action_rejects_empty_shortlist(monkeypatch):
    from eve_trader.actions import ActionError, do_start_refresh_shortlist

    monkeypatch.setattr(storage, "load_shortlist", lambda: [])
    with pytest.raises(ActionError, match="Shortlist is empty"):
        do_start_refresh_shortlist()


def test_start_job_is_the_generic_entry(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", _noop_fail_stale)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: None)
    monkeypatch.setattr(storage, "start_pipeline_run", lambda job, tool="trading": "run-g")
    monkeypatch.setattr(storage, "with_current_tenant", lambda fn: fn)

    class FakeThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(pipeline_runner.threading, "Thread", FakeThread)

    result = pipeline_runner.start_job(
        "doctrine", "sync_contracts", "Sync Contracts", lambda cb: {"ok": True},
    )
    assert result == {
        "run_id": "run-g", "status": "running",
        "job_name": "sync_contracts", "tool": "doctrine",
    }


def test_start_job_of_another_tool_is_rejected_while_trading_runs(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", _noop_fail_stale)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: {
        "run_id": "already", "status": "running",
        "job_name": JOB_REFRESH_AND_PRUNE, "tool": "trading",
    })
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started.append("start")

    monkeypatch.setattr(pipeline_runner.threading, "Thread", FakeThread)

    with pytest.raises(ConflictError, match="Search \\+ Add \\+ Clean Up is already running"):
        pipeline_runner.start_doctrine_sync()
    assert started == []


def test_job_status_ignores_a_running_job_for_a_different_tool(monkeypatch):
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job_name=None: {
        "run_id": "doc", "status": "running", "job_name": "sync_contracts", "tool": "doctrine",
    })
    monkeypatch.setattr(storage, "get_latest_pipeline_run", lambda job_name=None, tool=None: {
        "run_id": "old-trading", "status": "succeeded",
        "job_name": JOB_REFRESH_AND_PRUNE, "tool": tool,
    })
    status = pipeline_runner.job_status(pipeline_runner.TOOL_TRADING)
    assert status["run_id"] == "old-trading"
    assert pipeline_runner.job_status(pipeline_runner.TOOL_DOCTRINE)["run_id"] == "doc"


def test_doctrine_sync_worker_forwards_increasing_progress(monkeypatch):
    """start_doctrine_sync's worker must pass pipeline_runner's callback into
    do_sync_contracts (not a one-shot static message)."""
    from contextlib import contextmanager

    from eve_trader.doctrine import actions as doctrine_actions

    def fake_sync(progress_callback=None):
        assert progress_callback is not None
        progress_callback({"phase": "sync", "batch": 1, "total_batches": 3, "message": "Fetching contract items"})
        progress_callback({"phase": "sync", "batch": 2, "total_batches": 3, "message": "Fetching contract items"})
        progress_callback({"phase": "sync", "batch": 3, "total_batches": 3, "message": "Fetching contract items"})
        return {"contracts_synced": 3}

    monkeypatch.setattr(doctrine_actions, "do_sync_contracts", fake_sync)
    progresses = []
    monkeypatch.setattr(storage, "update_pipeline_run_progress", lambda run_id, p: progresses.append(p))
    finished = []
    monkeypatch.setattr(
        storage, "finish_pipeline_run",
        lambda run_id, status, result=None, error=None: finished.append((status, result)),
    )

    @contextmanager
    def _enter(tenant_id):
        yield

    monkeypatch.setattr(pipeline_runner.tenant_scope, "enter_tenant", _enter)
    pipeline_runner._run_doctrine_sync(_TENANT_ID, "run-doc")

    assert [p["batch"] for p in progresses] == [1, 2, 3]
    assert all(p["phase"] == "sync" for p in progresses)
    assert finished == [("succeeded", {"contracts_synced": 3})]


def test_sde_refresh_worker_forwards_increasing_progress(monkeypatch):
    from contextlib import contextmanager

    from eve_trader import admin as admin_mod

    def fake_refresh(progress_callback=None):
        assert progress_callback is not None
        progress_callback({"phase": "run", "batch": 1, "total_batches": 13, "message": "Fetching invTypes.csv"})
        progress_callback({"phase": "run", "batch": 2, "total_batches": 13, "message": "Fetching invGroups.csv"})
        return {"sde_types": 2}

    monkeypatch.setattr(admin_mod, "do_refresh_sde", fake_refresh)
    progresses = []
    monkeypatch.setattr(storage, "update_pipeline_run_progress", lambda run_id, p: progresses.append(p))
    finished = []
    monkeypatch.setattr(
        storage, "finish_pipeline_run",
        lambda run_id, status, result=None, error=None: finished.append((status, result)),
    )

    @contextmanager
    def _enter(tenant_id):
        yield

    monkeypatch.setattr(pipeline_runner.tenant_scope, "enter_tenant", _enter)
    pipeline_runner._run_sde_refresh(_TENANT_ID, "run-sde")

    assert [p["batch"] for p in progresses] == [1, 2]
    assert all(p["phase"] == "run" for p in progresses)
    assert finished == [("succeeded", {"sde_types": 2})]

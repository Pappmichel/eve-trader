"""Tests for the background Search + Add + Clean Up runner.

Lock behaviour and the with_current_tenant wrap are unit-tested with
storage/threading monkeypatches (no Postgres). Persistence + the unique
running-job index need a real DB.
"""
import pytest

from eve_trader import pipeline_runner, storage
from eve_trader.actions import ConflictError

from . import pg_helpers

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def test_second_start_rejected_when_a_run_is_already_running(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", lambda job: 0)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job: {
        "run_id": "already", "status": "running",
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


def test_start_wraps_the_worker_with_current_tenant(monkeypatch):
    wrapped = []
    original = storage.with_current_tenant

    def spy(fn):
        wrapped.append(fn)
        return original(fn)

    monkeypatch.setattr(storage, "with_current_tenant", spy)
    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", lambda job: 0)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job: None)
    monkeypatch.setattr(storage, "start_pipeline_run", lambda job: "run-1")

    captured = {}

    class FakeThread:
        def __init__(self, target=None, daemon=None, name=None):
            captured["target"] = target
            captured["daemon"] = daemon

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(pipeline_runner.threading, "Thread", FakeThread)

    result = pipeline_runner.start_refresh_and_prune(safe=False)

    assert result == {"run_id": "run-1", "status": "running"}
    assert wrapped, "storage.with_current_tenant must wrap the worker target"
    assert captured.get("started") is True
    assert captured.get("daemon") is True


def test_unique_violation_on_insert_maps_to_conflict(monkeypatch):
    from psycopg.errors import UniqueViolation

    monkeypatch.setattr(storage, "get_current_tenant", lambda: _TENANT_ID)
    monkeypatch.setattr(storage, "fail_stale_pipeline_runs", lambda job: 0)
    monkeypatch.setattr(storage, "get_running_pipeline_run", lambda job: None)

    def _raise(job):
        raise UniqueViolation("pipeline_runs_one_running")
    monkeypatch.setattr(storage, "start_pipeline_run", _raise)

    with pytest.raises(ConflictError, match="already running"):
        pipeline_runner.start_refresh_and_prune()

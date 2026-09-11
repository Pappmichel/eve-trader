"""Postgres persistence tests for pipeline_runs (docs/pipeline_runs_schema.sql)."""
import pytest

from eve_trader import storage
from eve_trader.pipeline_runner import JOB_REFRESH_AND_PRUNE

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_pipeline_runs_schema, tenant, tenant_pair  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def test_start_and_finish_pipeline_run(tenant, _apply_pipeline_runs_schema):
    run_id = storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)
    running = storage.get_running_pipeline_run(JOB_REFRESH_AND_PRUNE)
    assert running is not None
    assert running["run_id"] == run_id
    assert running["status"] == "running"

    storage.update_pipeline_run_progress(run_id, {"phase": "search", "batch": 1, "total_batches": 4, "evaluated": 10})
    storage.finish_pipeline_run(run_id, "succeeded", result={"added": 3})

    latest = storage.get_latest_pipeline_run(JOB_REFRESH_AND_PRUNE)
    assert latest["status"] == "succeeded"
    assert latest["progress"]["batch"] == 1
    assert latest["result"]["added"] == 3
    assert storage.get_running_pipeline_run(JOB_REFRESH_AND_PRUNE) is None


def test_second_running_insert_is_rejected(tenant, _apply_pipeline_runs_schema):
    storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)
    with pytest.raises(psycopg.errors.UniqueViolation):
        storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)


def test_second_running_insert_of_a_different_job_is_rejected(tenant, _apply_pipeline_runs_schema):
    storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)
    with pytest.raises(psycopg.errors.UniqueViolation):
        storage.start_pipeline_run("refresh_shortlist")
    running = storage.get_running_pipeline_run()
    assert running is not None
    assert running["job_name"] == JOB_REFRESH_AND_PRUNE


def test_pipeline_runs_are_isolated_per_tenant(tenant_pair, _apply_pipeline_runs_schema):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        run_a = storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)
    with storage.tenant_context(tenant_b):
        run_b = storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)
        assert storage.get_running_pipeline_run(JOB_REFRESH_AND_PRUNE)["run_id"] == run_b
    with storage.tenant_context(tenant_a):
        assert storage.get_running_pipeline_run(JOB_REFRESH_AND_PRUNE)["run_id"] == run_a

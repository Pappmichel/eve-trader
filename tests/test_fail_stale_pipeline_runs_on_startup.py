"""Startup clears leftover running pipeline_runs for every tenant."""
from __future__ import annotations

import pytest

from eve_trader import storage, tenant_scope
from eve_trader.api.app import fail_stale_pipeline_runs_on_startup
from eve_trader.pipeline_runner import JOB_REFRESH_AND_PRUNE

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_phase1_schema, _apply_pipeline_runs_schema, tenant_pair,
)

pytestmark = pg_helpers.postgres_required()


def test_startup_marks_running_rows_failed_for_every_tenant(tenant_pair, _apply_pipeline_runs_schema, monkeypatch):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)
    with storage.tenant_context(tenant_b):
        storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)

    monkeypatch.setattr(
        storage, "list_tenants",
        lambda: [(tenant_a, "a", None), (tenant_b, "b", None)],
    )
    fail_stale_pipeline_runs_on_startup()

    for tid in (tenant_a, tenant_b):
        with storage.tenant_context(tid):
            assert storage.get_running_pipeline_run() is None
            latest = storage.get_latest_pipeline_run(JOB_REFRESH_AND_PRUNE)
            assert latest["status"] == "failed"
            assert latest["error"] == "Stale running job (process restarted or hung)"

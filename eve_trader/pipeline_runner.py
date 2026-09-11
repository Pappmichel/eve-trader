"""Background runner for the user-triggered Trading pipeline.

Search + Add + Clean Up used to run inside the HTTP request handler
(POST /candidates/refresh-and-prune). With ships/blueprints in the
candidate universe that linear ESI work outlasts any proxy timeout - this
module moves the same `actions.do_refresh_and_prune_candidates` call onto a
stdlib `threading.Thread` and persists status in `pipeline_runs` so the
frontend can poll.

Deliberately not a job-queue library (Celery/RQ/APScheduler): same stance
as scheduler.py ("Deliberately not a real scheduling library"). One daemon
thread per start is enough; the unique partial index on pipeline_runs is
the cross-worker lock.

Polling, not LISTEN/NOTIFY: this app has no existing NOTIFY path, the
frontend already polls via react-query, and a ~4s lag on a minutes-long
job is the right complexity tradeoff.

The background thread uses tenant_scope.enter_tenant (not a bare
storage.set_current_tenant) so TRADING_CONFIG's per-tenant instance is
resolved too - ThreadPoolExecutor/raw threading.Thread do not inherit
contextvars. The submitted target is also wrapped in
storage.with_current_tenant, matching esi_client/production/esi_sync.
"""
from __future__ import annotations

import logging
import threading

from psycopg.errors import UniqueViolation

from . import storage, tenant_scope

log = logging.getLogger("eve_trader.pipeline_runner")

JOB_REFRESH_AND_PRUNE = "refresh_and_prune"


def start_refresh_and_prune(safe: bool = True) -> dict:
    """Inserts a running pipeline_runs row and starts the worker thread.
    Returns immediately with {run_id, status: "running"}. Raises
    ConflictError if this tenant already has a running refresh_and_prune."""
    from .actions import ConflictError

    tenant_id = storage.get_current_tenant()
    if tenant_id is None:
        from .actions import ActionError
        raise ActionError("No tenant in scope - cannot start a pipeline run.")

    storage.fail_stale_pipeline_runs(JOB_REFRESH_AND_PRUNE)
    running = storage.get_running_pipeline_run(JOB_REFRESH_AND_PRUNE)
    if running:
        raise ConflictError(
            "Search + Add + Clean Up is already running. Wait for it to finish, "
            "or reload the page to see its progress."
        )
    try:
        run_id = storage.start_pipeline_run(JOB_REFRESH_AND_PRUNE)
    except UniqueViolation as e:
        raise ConflictError(
            "Search + Add + Clean Up is already running. Wait for it to finish, "
            "or reload the page to see its progress."
        ) from e

    worker = storage.with_current_tenant(
        lambda: _run_refresh_and_prune(tenant_id, run_id, safe)
    )
    threading.Thread(target=worker, daemon=True, name=f"pipeline-{run_id[:8]}").start()
    return {"run_id": run_id, "status": "running"}


def _run_refresh_and_prune(tenant_id: str, run_id: str, safe: bool) -> None:
    """Tenant-scoped worker body. enter_tenant is required even though the
    target is already wrapped in with_current_tenant: that wrapper only
    restores storage's ambient tenant_id, not TRADING_CONFIG's per-tenant
    instance (see tenant_scope.enter_tenant / CLAUDE.md)."""
    from . import actions

    with tenant_scope.enter_tenant(tenant_id):
        def progress_cb(progress: dict) -> None:
            try:
                storage.update_pipeline_run_progress(run_id, progress)
            except Exception:  # noqa: BLE001 - a status write must never abort the job
                log.exception("Failed to persist pipeline progress for run %s", run_id)

        try:
            result = actions.do_refresh_and_prune_candidates(
                safe=safe, progress_callback=progress_cb)
            storage.finish_pipeline_run(run_id, "succeeded", result=result)
        except Exception as e:  # noqa: BLE001 - the HTTP request already returned
            log.exception("Pipeline run %s failed", run_id)
            try:
                storage.finish_pipeline_run(run_id, "failed", error=str(e))
            except Exception:  # noqa: BLE001
                log.exception("Failed to persist pipeline failure for run %s", run_id)

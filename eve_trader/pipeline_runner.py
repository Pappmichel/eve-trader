"""Background runner for user-triggered Trading jobs.

Search + Add + Clean Up, Refresh Shortlist, and Run Complete Pipeline used
to run inside the HTTP request handler. With ships/blueprints in the
candidate universe that linear ESI work outlasts any proxy timeout - this
module moves the same `actions.do_*` calls onto a stdlib `threading.Thread`
and persists status in `pipeline_runs` so the frontend can poll.

The three jobs share one per-tenant lock (unique partial index on
`pipeline_runs (tenant_id) WHERE status='running'`): they all mutate the
shortlist / snapshot, so overlapping them would race. The scheduler and
CLI still call `do_pipeline` / `do_refresh_shortlist` in-process - they
never go through this runner.

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
from typing import Callable

from psycopg.errors import UniqueViolation

from . import storage, tenant_scope

log = logging.getLogger("eve_trader.pipeline_runner")

JOB_REFRESH_AND_PRUNE = "refresh_and_prune"
JOB_REFRESH_SHORTLIST = "refresh_shortlist"
JOB_PIPELINE = "pipeline"

_JOB_LABELS = {
    JOB_REFRESH_AND_PRUNE: "Search + Add + Clean Up",
    JOB_REFRESH_SHORTLIST: "Refresh Shortlist",
    JOB_PIPELINE: "Run Complete Pipeline",
}


def _conflict_message(running: dict | None = None) -> str:
    job_name = (running or {}).get("job_name")
    label = _JOB_LABELS.get(job_name, "A Trading job")
    return (
        f"{label} is already running. Wait for it to finish, "
        "or reload the page to see its progress."
    )


def start_refresh_and_prune(safe: bool = True) -> dict:
    """Inserts a running pipeline_runs row and starts the worker thread.
    Returns immediately with {run_id, status: "running"}. Raises
    ConflictError if this tenant already has any running Trading job."""
    return _start_job(
        JOB_REFRESH_AND_PRUNE,
        lambda tenant_id, run_id: _run_refresh_and_prune(tenant_id, run_id, safe),
    )


def start_refresh_shortlist() -> dict:
    """Background Refresh Shortlist - same lock as Search + Add + Clean Up
    and Run Complete Pipeline. Raises ConflictError if any of them is
    already running for this tenant."""
    return _start_job(
        JOB_REFRESH_SHORTLIST,
        lambda tenant_id, run_id: _run_refresh_shortlist(tenant_id, run_id),
    )


def start_pipeline(safe: bool = True, rebuild_universe: bool = False) -> dict:
    """Background Run Complete Pipeline. Scheduler/CLI still call
    actions.do_pipeline in-process; this is only the HTTP path."""
    return _start_job(
        JOB_PIPELINE,
        lambda tenant_id, run_id: _run_pipeline(tenant_id, run_id, safe, rebuild_universe),
    )


def _start_job(job_name: str, worker: Callable[[str, str], None]) -> dict:
    from .actions import ActionError, ConflictError

    tenant_id = storage.get_current_tenant()
    if tenant_id is None:
        raise ActionError("No tenant in scope - cannot start a pipeline run.")

    storage.fail_stale_pipeline_runs()
    running = storage.get_running_pipeline_run()
    if running:
        raise ConflictError(_conflict_message(running))
    try:
        run_id = storage.start_pipeline_run(job_name)
    except UniqueViolation as e:
        raise ConflictError(_conflict_message(storage.get_running_pipeline_run())) from e

    wrapped = storage.with_current_tenant(lambda: worker(tenant_id, run_id))
    threading.Thread(target=wrapped, daemon=True, name=f"{job_name}-{run_id[:8]}").start()
    return {"run_id": run_id, "status": "running", "job_name": job_name}


def _execute(tenant_id: str, run_id: str, work: Callable) -> None:
    """Tenant-scoped worker body. enter_tenant is required even though the
    target is already wrapped in with_current_tenant: that wrapper only
    restores storage's ambient tenant_id, not TRADING_CONFIG's per-tenant
    instance (see tenant_scope.enter_tenant / CLAUDE.md)."""
    with tenant_scope.enter_tenant(tenant_id):
        def progress_cb(progress: dict) -> None:
            try:
                storage.update_pipeline_run_progress(run_id, progress)
            except Exception:  # noqa: BLE001 - a status write must never abort the job
                log.exception("Failed to persist pipeline progress for run %s", run_id)

        try:
            result = work(progress_cb)
            storage.finish_pipeline_run(run_id, "succeeded", result=result)
        except Exception as e:  # noqa: BLE001 - the HTTP request already returned
            log.exception("Pipeline run %s failed", run_id)
            try:
                storage.finish_pipeline_run(run_id, "failed", error=str(e))
            except Exception:  # noqa: BLE001
                log.exception("Failed to persist pipeline failure for run %s", run_id)


def _run_refresh_and_prune(tenant_id: str, run_id: str, safe: bool) -> None:
    from . import actions
    _execute(
        tenant_id, run_id,
        lambda cb: actions.do_refresh_and_prune_candidates(safe=safe, progress_callback=cb),
    )


def _run_refresh_shortlist(tenant_id: str, run_id: str) -> None:
    from . import actions
    _execute(
        tenant_id, run_id,
        lambda cb: actions.do_refresh_shortlist(progress_callback=cb),
    )


def _run_pipeline(tenant_id: str, run_id: str, safe: bool, rebuild_universe: bool) -> None:
    from . import actions
    _execute(
        tenant_id, run_id,
        lambda cb: actions.do_pipeline(
            safe=safe, rebuild_universe=rebuild_universe, progress_callback=cb),
    )

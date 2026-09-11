"""Background runner for user-triggered jobs across tools.

Trading (Search + Add + Clean Up / Refresh Shortlist / Pipeline), Doctrine
(contract sync) and Admin (SDE refresh) used to run inside the HTTP request
handler. Linear ESI / multi-CSV work outlasts any proxy timeout - this
module moves the same `do_*` calls onto a stdlib `threading.Thread` and
persists status in `pipeline_runs` so the frontend can poll.

`tool` is a real column on pipeline_runs (not a `doctrine:sync` prefix on
job_name): per-tool status pollers must not surface another tool's run as
their own progress. job_name stays unqualified within the tool.

Lock is one running job per tenant, not per (tenant, tool). ESI rate limits
and the 10-conn app pool are process-wide; overlapping a Trading pipeline
with a Doctrine sync on the same tenant is resource contention. The
scheduler and CLI still call `do_*` in-process - they never go through
this runner.

Deliberately not a job-queue library (Celery/RQ/APScheduler): same stance
as scheduler.py ("Deliberately not a real scheduling library"). One daemon
thread per start is enough; the unique partial index on pipeline_runs is
the cross-worker lock.

Polling, not LISTEN/NOTIFY: this app has no existing NOTIFY path, the
frontend already polls via react-query, and a ~4s lag on a minutes-long
job is the right complexity tradeoff.

The background thread uses tenant_scope.enter_tenant (not a bare
storage.set_current_tenant) so TRADING_CONFIG/PRODUCTION_CONFIG's
per-tenant instance is resolved too - ThreadPoolExecutor/raw
threading.Thread do not inherit contextvars. The submitted target is also
wrapped in storage.with_current_tenant, matching esi_client/production/
esi_sync. Every new job goes through start_job, which is the one place
that wrap lives.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from psycopg.errors import UniqueViolation

from . import storage, tenant_scope

log = logging.getLogger("eve_trader.pipeline_runner")

TOOL_TRADING = "trading"
TOOL_DOCTRINE = "doctrine"
TOOL_ADMIN = "admin"

JOB_REFRESH_AND_PRUNE = "refresh_and_prune"
JOB_REFRESH_SHORTLIST = "refresh_shortlist"
JOB_PIPELINE = "pipeline"
JOB_SYNC_CONTRACTS = "sync_contracts"
JOB_SDE_REFRESH = "sde_refresh"

_JOB_LABELS: dict[tuple[str, str], str] = {
    (TOOL_TRADING, JOB_REFRESH_AND_PRUNE): "Search + Add + Clean Up",
    (TOOL_TRADING, JOB_REFRESH_SHORTLIST): "Refresh Shortlist",
    (TOOL_TRADING, JOB_PIPELINE): "Run Complete Pipeline",
    (TOOL_DOCTRINE, JOB_SYNC_CONTRACTS): "Sync Contracts",
    (TOOL_ADMIN, JOB_SDE_REFRESH): "Refresh SDE",
}

# worker(progress_callback) -> result dict (or any JSON-serializable value)
Worker = Callable[[Callable[[dict], None]], object]


def _conflict_message(running: dict | None = None) -> str:
    if not running:
        return (
            "A background job is already running. Wait for it to finish, "
            "or reload the page to see its progress."
        )
    tool = running.get("tool") or TOOL_TRADING
    job_name = running.get("job_name")
    label = _JOB_LABELS.get((tool, job_name), job_name or "A background job")
    return (
        f"{label} is already running. Wait for it to finish, "
        "or reload the page to see its progress."
    )


def start_job(tool: str, job_name: str, label: str, worker: Worker) -> dict:
    """Inserts a running pipeline_runs row and starts `worker(progress_cb)`
    on a daemon thread wrapped in with_current_tenant + enter_tenant.
    Returns immediately with {run_id, status: "running", job_name, tool}.
    Raises ConflictError if this tenant already has any running job."""
    from .actions import ActionError, ConflictError

    _JOB_LABELS[(tool, job_name)] = label

    tenant_id = storage.get_current_tenant()
    if tenant_id is None:
        raise ActionError("No tenant in scope - cannot start a pipeline run.")

    storage.fail_stale_pipeline_runs()
    running = storage.get_running_pipeline_run()
    if running:
        raise ConflictError(_conflict_message(running))
    try:
        run_id = storage.start_pipeline_run(job_name, tool=tool)
    except UniqueViolation as e:
        raise ConflictError(_conflict_message(storage.get_running_pipeline_run())) from e

    wrapped = storage.with_current_tenant(
        lambda: _execute(tenant_id, run_id, worker)
    )
    threading.Thread(
        target=wrapped, daemon=True, name=f"{tool}-{job_name}-{run_id[:8]}",
    ).start()
    return {"run_id": run_id, "status": "running", "job_name": job_name, "tool": tool}


def job_status(tool: str) -> dict:
    """Currently-running job for `tool` on this tenant, else this tool's
    latest finished row, else idle. A different tool's running job is
    intentionally not returned here (the shared lock still 409s a start);
    each UI poller only shows its own progress."""
    running = storage.get_running_pipeline_run()
    running_tool = (running or {}).get("tool") or TOOL_TRADING
    if running and running_tool == tool:
        return running
    row = storage.get_latest_pipeline_run(tool=tool)
    if row is None:
        return {
            "run_id": None, "job_name": None, "tool": tool, "status": "idle",
            "progress": None, "result": None, "error": None,
        }
    return row


def start_refresh_and_prune(safe: bool = True) -> dict:
    """Inserts a running pipeline_runs row and starts the worker thread.
    Returns immediately with {run_id, status: "running"}. Raises
    ConflictError if this tenant already has any running job."""
    from . import actions
    return start_job(
        TOOL_TRADING, JOB_REFRESH_AND_PRUNE,
        _JOB_LABELS[(TOOL_TRADING, JOB_REFRESH_AND_PRUNE)],
        lambda cb: actions.do_refresh_and_prune_candidates(safe=safe, progress_callback=cb),
    )


def start_refresh_shortlist() -> dict:
    """Background Refresh Shortlist. Raises ConflictError if any job is
    already running for this tenant."""
    from . import actions
    return start_job(
        TOOL_TRADING, JOB_REFRESH_SHORTLIST,
        _JOB_LABELS[(TOOL_TRADING, JOB_REFRESH_SHORTLIST)],
        lambda cb: actions.do_refresh_shortlist(progress_callback=cb),
    )


def start_pipeline(safe: bool = True, rebuild_universe: bool = False) -> dict:
    """Background Run Complete Pipeline. Scheduler/CLI still call
    actions.do_pipeline in-process; this is only the HTTP path."""
    from . import actions
    return start_job(
        TOOL_TRADING, JOB_PIPELINE,
        _JOB_LABELS[(TOOL_TRADING, JOB_PIPELINE)],
        lambda cb: actions.do_pipeline(
            safe=safe, rebuild_universe=rebuild_universe, progress_callback=cb),
    )


def start_doctrine_sync() -> dict:
    """Background Doctrine contract sync. Scheduler still calls
    doctrine.actions.do_sync_contracts in-process."""
    from .doctrine import actions as doctrine_actions
    return start_job(
        TOOL_DOCTRINE, JOB_SYNC_CONTRACTS,
        _JOB_LABELS[(TOOL_DOCTRINE, JOB_SYNC_CONTRACTS)],
        lambda cb: _run_with_message(cb, "Syncing contracts", doctrine_actions.do_sync_contracts),
    )


def start_sde_refresh() -> dict:
    """Background Admin SDE refresh. Scheduler/CLI still call
    admin.do_refresh_sde in-process if they ever did; this is the HTTP path."""
    from . import admin as admin_mod
    return start_job(
        TOOL_ADMIN, JOB_SDE_REFRESH,
        _JOB_LABELS[(TOOL_ADMIN, JOB_SDE_REFRESH)],
        lambda cb: _run_with_message(cb, "Refreshing SDE", admin_mod.do_refresh_sde),
    )


def _run_with_message(progress_cb, message: str, fn: Callable) -> object:
    try:
        progress_cb({"phase": "run", "message": message})
    except Exception:  # noqa: BLE001
        log.exception("progress_callback failed")
    return fn()


def _execute(tenant_id: str, run_id: str, work: Worker) -> None:
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

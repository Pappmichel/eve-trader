"""Lightweight in-process background scheduler - a single daemon thread that
wakes up periodically and runs whichever registered job is due, based on each
job's own last-run timestamp. trading_pipeline/production_sync reuse storage.
esi_sync_state (the table do_pipeline and production's do_sync_esi already
write to via set_esi_sync_time) as the "when did this last run" source; the
backup job reuses the newest backup file's own mtime the same way (see
backup.list_backups) - no separate scheduler-specific persistence needed
anywhere, and a run triggered manually from the UI also counts, correctly
pushing back the next scheduled run either way.

Deliberately not a real scheduling library (APScheduler etc.): this app has
exactly three jobs, all already idempotent and already isolate their own
failures internally (do_pipeline wraps each step in try/except; do_sync_esi
wraps each character/corp fetch in try/except; create_backup either fully
succeeds or raises, nothing partial to isolate) - a stdlib thread + sleep
loop covers this without a new dependency.

Multi-tenant (Phase 4 of docs/MULTI_TENANT_PLAN.md): trading_pipeline/
production_sync run once per tick *per tenant* (storage.list_tenants()),
each fully scoped via tenant_scope.enter_tenant - a tenant's own
TradingConfig.scheduler_enabled/interval fields decide whether *their* jobs
run, independently of any other tenant. backup stays a single **global**,
unscoped job - one pg_dump of the whole Postgres database already captures
every tenant's RLS-scoped data in one shot (see backup.py), so there's
nothing to iterate. Whether the background thread runs *at all* is a
separate, operator-level decision, read from DEFAULT_TENANT_ID's own
config (see start()) - this app's own "trusted single operator by default"
convention, same as storage.DEFAULT_TENANT_ID everywhere else. Off by
default (TradingConfig.scheduler_enabled) - opt-in, since this is a
credentials-handling tool making its own ESI calls in the background, which
should never start happening without the user explicitly asking for it.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Optional

from . import backup, storage, tenant_scope
from .config import TRADING_CONFIG, TradingConfig
from .production import jita_price_cache

log = logging.getLogger("eve_trader.scheduler")

CHECK_INTERVAL_SECONDS = 300  # how often the background thread wakes up to check what's due

_thread: threading.Thread | None = None
_stop_event = threading.Event()

# {tenant_id: {job_name: {"ran_at": iso str, "error": str | None}}} - last
# outcome of each tenant's trading_pipeline/production_sync (whether
# triggered by the scheduler or not, for the "ran_at" part - see
# get_status), surfaced via the portfolio router for a small status readout
# in the UI.
last_run_status: dict[str, dict[str, dict]] = {}

# The global backup job's own last outcome - not tenant-keyed, since backup
# itself isn't per-tenant (see module docstring).
_backup_status: dict = {}

# Same shape as _backup_status, for the other global/unscoped job - the
# shared Jita price cache (production/jita_price_cache.py) prices public,
# tenant-independent ESI data, so like backup it runs once per tick, not
# once per tenant.
_jita_price_cache_status: dict = {}


def _hours_since(iso_ts: str | None) -> float:
    if iso_ts is None:
        return float("inf")  # never run - always due
    since = dt.datetime.fromisoformat(iso_ts)
    if since.tzinfo is None:
        since = since.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - since).total_seconds() / 3600


def _hours_since_last_backup() -> float:
    backups = backup.list_backups()
    return _hours_since(backups[0]["created_at"]) if backups else float("inf")


_GLOBAL_JOB_STATUS = {"backup": _backup_status, "jita_price_cache": _jita_price_cache_status}


def _run_job(tenant_id: Optional[str], name: str, fn) -> None:
    """tenant_id=None records into the matching global status dict (looked
    up by `name` via _GLOBAL_JOB_STATUS) instead of a per-tenant slot - only
    the backup and jita_price_cache jobs ever pass None."""
    try:
        fn()
        result = {"ran_at": dt.datetime.now(dt.timezone.utc).isoformat(), "error": None}
    except Exception as e:  # noqa: BLE001 - a job's own failure must never kill the scheduler thread
        log.warning("Scheduled job %r (tenant=%s) failed: %s", name, tenant_id, e)
        result = {"ran_at": dt.datetime.now(dt.timezone.utc).isoformat(), "error": str(e)}

    if tenant_id is None:
        _GLOBAL_JOB_STATUS[name].update(result)
    else:
        last_run_status.setdefault(tenant_id, {})[name] = result


def _check_and_run_due_jobs_for_tenant(tenant_id: str, cfg: TradingConfig) -> None:
    """The per-tenant trading_pipeline/production_sync check - takes `cfg`
    explicitly (rather than reading the ambient TRADING_CONFIG itself) so
    it stays directly unit-testable the way it already was pre-Phase-4;
    the caller (_check_and_run_due_jobs) is what resolves `cfg` for
    `tenant_id` via tenant_scope.enter_tenant before calling this."""
    # Lazy imports: actions.py, production/actions.py, and doctrine/actions.py
    # all import from this same config module - importing them at module
    # load time would risk a circular import; deferring to call time avoids
    # that without restructuring any of the three actions modules.
    from . import actions
    from .doctrine import actions as doctrine_actions
    from .production import actions as production_actions

    if not cfg.scheduler_enabled:
        return

    if _hours_since(storage.get_esi_sync_time("trading")) >= cfg.trading_pipeline_interval_hours:
        _run_job(tenant_id, "trading_pipeline", lambda: actions.do_pipeline(safe=True))

    if _hours_since(storage.get_esi_sync_time("production")) >= cfg.production_sync_interval_hours:
        _run_job(tenant_id, "production_sync", production_actions.do_sync_esi)

    if _hours_since(storage.get_esi_sync_time("doctrine")) >= cfg.doctrine_sync_interval_hours:
        _run_job(tenant_id, "doctrine_contract_sync", doctrine_actions.do_sync_contracts)


def _check_and_run_backup_job() -> None:
    """Global, unscoped - reads DEFAULT_TENANT_ID's own backup_interval_hours
    (the operator's setting), same reasoning as start()'s own gate."""
    with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
        interval_hours = TRADING_CONFIG.backup_interval_hours
    if _hours_since_last_backup() >= interval_hours:
        _run_job(None, "backup", backup.create_backup)


def _check_and_run_jita_price_cache_job() -> None:
    """Global, unscoped, same reasoning as _check_and_run_backup_job - the
    cache itself (production/jita_price_cache.py) prices public, tenant-
    independent ESI data, so this reads DEFAULT_TENANT_ID's own
    jita_price_cache_interval_hours (the operator's setting) rather than
    iterating tenants. last_updated_at() (not a per-tenant esi_sync_state
    row) is this job's own "when did this last happen" source - shared by
    both this scheduled tick and the standalone manual admin action
    (admin.do_refresh_jita_price_cache), so a manual run correctly pushes
    back the next scheduled one too, same as backup's own mtime-based
    check."""
    with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
        interval_hours = TRADING_CONFIG.jita_price_cache_interval_hours
    if _hours_since(jita_price_cache.last_updated_at()) >= interval_hours:
        _run_job(None, "jita_price_cache", jita_price_cache.refresh_jita_price_cache)


def _check_and_run_due_jobs() -> None:
    for tenant_id, _name, _created_at in storage.list_tenants():
        tenant_id = str(tenant_id)
        try:
            with tenant_scope.enter_tenant(tenant_id):
                _check_and_run_due_jobs_for_tenant(tenant_id, TRADING_CONFIG)
        except Exception as e:  # noqa: BLE001 - one tenant's failure must not block the others
            log.warning("Per-tenant job check failed for tenant %s: %s", tenant_id, e)

    _check_and_run_backup_job()
    _check_and_run_jita_price_cache_job()


def _loop() -> None:
    while not _stop_event.is_set():
        try:
            _check_and_run_due_jobs()
        except Exception as e:  # noqa: BLE001 - one bad check must not end the background thread
            log.warning("Scheduler check failed: %s", e)
        _stop_event.wait(CHECK_INTERVAL_SECONDS)


def start() -> None:
    """Starts the background thread if DEFAULT_TENANT_ID's own
    scheduler_enabled is set and not already running - an operator-level
    "does the automated background thread run at all" decision, distinct
    from each tenant's own opt-in (checked per-tenant on every tick, see
    _check_and_run_due_jobs_for_tenant). Safe to call more than once - only
    the first call (while no thread is alive) actually starts anything, so
    app.py's startup hook doesn't need to track whether it already ran."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
        enabled = TRADING_CONFIG.scheduler_enabled
    if not enabled:
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="eve-trader-scheduler")
    _thread.start()


def stop() -> None:
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)


def get_status() -> dict:
    """Read-only snapshot for the UI: whether the background thread is
    enabled/running, plus each job's configured interval and last outcome -
    "last_run_at" comes from storage/the backup directory itself (so it
    reflects a manual run too, not just scheduler-triggered ones),
    "last_error" only from an actual scheduler-triggered attempt (a manual
    run's own error already surfaces directly in the UI at the time it
    happens). Reads the *ambient* TRADING_CONFIG (already resolved for the
    calling request's own tenant by AccessGateMiddleware) and that tenant's
    own last_run_status slot - "backup"'s interval_hours is shown from this
    same ambient tenant's perspective for convenience, though the value
    that actually governs the global backup job's timing is always
    DEFAULT_TENANT_ID's (see _check_and_run_backup_job)."""
    tenant_id = storage.get_current_tenant()
    tenant_jobs = last_run_status.get(tenant_id, {}) if tenant_id else {}
    backups = backup.list_backups()
    return {
        "enabled": TRADING_CONFIG.scheduler_enabled,
        "running": _thread is not None and _thread.is_alive(),
        "jobs": {
            "trading_pipeline": {
                "interval_hours": TRADING_CONFIG.trading_pipeline_interval_hours,
                "last_run_at": storage.get_esi_sync_time("trading"),
                "last_error": tenant_jobs.get("trading_pipeline", {}).get("error"),
            },
            "production_sync": {
                "interval_hours": TRADING_CONFIG.production_sync_interval_hours,
                "last_run_at": storage.get_esi_sync_time("production"),
                "last_error": tenant_jobs.get("production_sync", {}).get("error"),
            },
            "doctrine_contract_sync": {
                "interval_hours": TRADING_CONFIG.doctrine_sync_interval_hours,
                "last_run_at": storage.get_esi_sync_time("doctrine"),
                "last_error": tenant_jobs.get("doctrine_contract_sync", {}).get("error"),
            },
            "backup": {
                "interval_hours": TRADING_CONFIG.backup_interval_hours,
                "last_run_at": backups[0]["created_at"] if backups else None,
                "last_error": _backup_status.get("error"),
            },
            "jita_price_cache": {
                "interval_hours": TRADING_CONFIG.jita_price_cache_interval_hours,
                "last_run_at": jita_price_cache.last_updated_at(),
                "last_error": _jita_price_cache_status.get("error"),
            },
        },
    }

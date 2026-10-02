"""Lightweight in-process background scheduler - a single daemon thread that
wakes up periodically and runs whichever registered job is due, based on each
job's own last-run timestamp. trading_pipeline reuses storage.esi_sync_state
(the table do_pipeline already writes via set_esi_sync_time) as the "when
did this last run" source; esi_data_sync asks the orchestrator
(`do_sync_due`) which (owner, kind) pairs are due given esi_freshness and
the three tier intervals. The backup job reuses the newest backup file's
own mtime the same way (see backup.list_backups) - no separate
scheduler-specific persistence needed anywhere, and a run triggered
manually from the UI also counts: do_pipeline writes esi_sync_state, a
manual tool sync stamps esi_freshness and pushes those pairs past the
next scheduled fetch.

Deliberately not a real scheduling library (APScheduler etc.): this app has
a handful of jobs, all already idempotent and already isolate their own
failures internally (do_pipeline wraps each step in try/except; do_sync_due
isolates per owner; create_backup either fully succeeds or raises) - a
stdlib thread + sleep loop covers this without a new dependency.

Multi-tenant (Phase 4 of docs/MULTI_TENANT_PLAN.md): trading_pipeline /
esi_data_sync run once per tick *per tenant* (storage.list_tenants()),
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

import contextvars
import datetime as dt
import logging
import threading
from typing import Optional

from . import backup, portfolio, storage, tenant_eligibility, tenant_scope
from .config import SCHEDULER_OPERATOR_CONFIG, TRADING_CONFIG, TradingConfig
from .production import jita_price_cache

log = logging.getLogger("eve_trader.scheduler")

CHECK_INTERVAL_SECONDS = 300  # how often the background thread wakes up to check what's due

# Per-job wall-clock cap. Tenants run sequentially on this one scheduler
# thread; a hung ESI call would otherwise delay every later tenant plus the
# global backup/jita-cache jobs on the same tick.
#
# A short-lived worker thread + join(timeout) lets the tick move on. The
# worker is not killed (unsafe with the connection pool / ESI session) - it
# keeps running in the background. Overlap is acceptable at this app's
# invite-only scale (well under 10 tenants, see CLAUDE.md's connection-pool
# note). If tenant count routinely exceeds ~10, or a single job regularly
# exceeds this timeout while still making progress, raise the cap before
# adding a real job-queue.
JOB_TIMEOUT_SECONDS = 900  # 15 minutes

_thread: threading.Thread | None = None
_stop_event = threading.Event()

# {tenant_id: {job_name: {"ran_at": iso str, "error": str | None}}} - last
# outcome of each tenant's trading_pipeline/esi_data_sync (whether
# triggered by the scheduler or not, for the "ran_at" part - see
# get_status), surfaced via the portfolio router for a small status readout
# in the UI. Per-kind ESI state lives on esi_freshness, not here.
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


def _portfolio_snapshot_due(cfg: TradingConfig) -> bool:
    """Calendar-date based, unlike every other job's `_hours_since` check -
    deliberately so (confirmed real bug in review): portfolio_snapshots'
    own primary key is one row per calendar day, but `_hours_since`-style
    "now - last taken_at >= interval_hours" drifts a few minutes later
    each successful run (each run's own `taken_at` is whenever that tick
    happened to fire, not a fixed time of day) - over enough days that
    drift can push the next run past midnight and skip a calendar day
    outright, something an hours-since check can never self-correct
    because it only ever compares against the *last actual run*, not the
    calendar. `portfolio_snapshot_interval_hours` still governs cadence,
    just rounded to whole days (minimum 1) since that is this table's own
    real granularity - a sub-24h value would otherwise request more than
    one snapshot per day, which the table's (tenant_id, snapshot_date)
    primary key cannot represent anyway."""
    latest = storage.latest_portfolio_snapshot_date()
    if latest is None:
        return True
    interval_days = max(1, round(cfg.portfolio_snapshot_interval_hours / 24))
    return (dt.date.today() - latest).days >= interval_days


def _job_due(success_ts: str | None, attempt_ts: str | None, interval_hours: float) -> bool:
    """Interval since the last success *and* failure backoff since the last
    attempt (docs/SCHEDULER_REWORK_PLAN.md decision 6). Without the second
    half a job that keeps failing before it can record a success (ESI down, no
    token, `pg_dump` unavailable) would re-run on every 5-minute tick. After a
    success the attempt is never newer than the success stamp's interval, so
    this only bites after a failed attempt. `attempt_ts` is the in-process
    outcome recorded by `_run_job`, so a restart allows one immediate retry."""
    from .esi_data.orchestrator import FAILURE_BACKOFF_HOURS  # lazy: see _check_and_run_due_jobs_for_tenant

    if _hours_since(success_ts) < interval_hours:
        return False
    if attempt_ts is not None and _hours_since(attempt_ts) < min(interval_hours, FAILURE_BACKOFF_HOURS):
        return False
    return True


def _last_attempt(tenant_id: Optional[str], name: str) -> str | None:
    if tenant_id is None:
        return _GLOBAL_JOB_STATUS[name].get("ran_at")
    return last_run_status.get(tenant_id, {}).get(name, {}).get("ran_at")


def _master_enabled() -> bool:
    """The operator-level master switch: DEFAULT_TENANT_ID's own
    `scheduler_enabled`. Tenant-level jobs and the global backup/Jita jobs
    need it in addition to their own switch."""
    with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
        return bool(TRADING_CONFIG.scheduler_enabled)


_GLOBAL_JOB_STATUS = {"backup": _backup_status, "jita_price_cache": _jita_price_cache_status}


def _run_job(tenant_id: Optional[str], name: str, fn) -> None:
    """tenant_id=None records into the matching global status dict (looked
    up by `name` via _GLOBAL_JOB_STATUS) instead of a per-tenant slot - only
    the backup and jita_price_cache jobs ever pass None.

    Runs `fn` on a short-lived daemon thread so a hang cannot stall later
    tenants on this tick (see JOB_TIMEOUT_SECONDS). contextvars.copy_context
    carries the submitting thread's tenant/config into the worker - raw
    threading.Thread does not inherit contextvars (CLAUDE.md)."""
    outcome: dict = {"error": None}

    def _worker() -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - a job's own failure must never kill the scheduler thread
            log.warning("Scheduled job %r (tenant=%s) failed: %s", name, tenant_id, e)
            outcome["error"] = str(e)

    ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=ctx.run, args=(_worker,),
        daemon=True, name=f"eve-trader-job-{name}",
    )
    thread.start()
    thread.join(JOB_TIMEOUT_SECONDS)
    if thread.is_alive():
        log.warning(
            "Scheduled job %r (tenant=%s) exceeded %ss timeout - still running in the "
            "background; later tenants on this tick will proceed",
            name, tenant_id, JOB_TIMEOUT_SECONDS,
        )
        outcome["error"] = f"timed out after {JOB_TIMEOUT_SECONDS}s (still running)"

    result = {
        "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "error": outcome["error"],
    }

    if tenant_id is None:
        _GLOBAL_JOB_STATUS[name].update(result)
    else:
        last_run_status.setdefault(tenant_id, {})[name] = result


def _check_and_run_due_jobs_for_tenant(
    tenant_id: str, cfg: TradingConfig, *, active: bool = True, master_enabled: bool = True,
) -> None:
    """The per-tenant trading_pipeline / esi_data_sync / portfolio_snapshot
    check - takes `cfg` explicitly (rather than reading the ambient
    TRADING_CONFIG itself) so it stays directly unit-testable the way it
    already was pre-Phase-4; the caller (_check_and_run_due_jobs) is what
    resolves `cfg` for `tenant_id` via tenant_scope.enter_tenant before
    calling this.

    `active` is the tenant inactivity gate (tenant_eligibility.is_active); an
    inactive tenant runs nothing here. Alert demand (Discord feature) will
    later be the one thing an inactive tenant still syncs - that hook is
    docs/SCHEDULER_REWORK_PLAN.md's "Gate split".
    `master_enabled` is the operator switch (Default tenant's
    scheduler_enabled): these legacy jobs need it *and* the tenant's own flag.
    """
    # Lazy imports: actions.py and esi_data.orchestrator import from this
    # same config module - importing them at module load time would risk a
    # circular import; deferring to call time avoids that.
    from . import actions
    from .esi_data import orchestrator as esi_orchestrator

    if not master_enabled or not cfg.scheduler_enabled:
        return
    if not active:
        return

    # None = no restriction (gate off); otherwise the tenant's current grants.
    grants = tenant_eligibility.granted_tools(tenant_id)

    if tenant_eligibility.may_use("trading", grants) and _job_due(
        storage.get_esi_sync_time("trading"),
        _last_attempt(tenant_id, "trading_pipeline"),
        cfg.trading_pipeline_interval_hours,
    ):
        _run_job(tenant_id, "trading_pipeline", lambda: actions.do_pipeline(safe=True))

    # Cheap inline check (two small reads): only start a job thread - and write
    # a status entry - when something is actually due. do_sync_due re-derives
    # the same list, so the two cannot disagree.
    if esi_orchestrator.pending_due(granted_tools=grants):
        _run_job(
            tenant_id, "esi_data_sync",
            lambda: esi_orchestrator.do_sync_due(granted_tools=grants),
        )

    if _portfolio_snapshot_due(cfg):
        _run_job(tenant_id, "portfolio_snapshot", lambda: portfolio.take_portfolio_snapshot(cfg))


def _check_and_run_backup_job() -> None:
    """Global, unscoped - reads DEFAULT_TENANT_ID's own backup_interval_hours
    (the operator's setting), same reasoning as start()'s own gate. Skipped
    when the operator switched the job off (backup_job_enabled)."""
    if not SCHEDULER_OPERATOR_CONFIG.backup_job_enabled:
        return
    with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
        interval_hours = TRADING_CONFIG.backup_interval_hours
    backups = backup.list_backups()
    newest = backups[0]["created_at"] if backups else None
    if _job_due(newest, _last_attempt(None, "backup"), interval_hours):
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
    check. Skipped when the operator switched the job off
    (jita_price_cache_job_enabled)."""
    if not SCHEDULER_OPERATOR_CONFIG.jita_price_cache_job_enabled:
        return
    with tenant_scope.enter_tenant(storage.DEFAULT_TENANT_ID):
        interval_hours = TRADING_CONFIG.jita_price_cache_interval_hours
    if _job_due(
        jita_price_cache.last_updated_at(), _last_attempt(None, "jita_price_cache"), interval_hours,
    ):
        _run_job(None, "jita_price_cache", jita_price_cache.refresh_jita_price_cache)


def _check_and_run_alerts_job() -> None:
    """Per tenant with an enabled alert opt-in. Independent of the tenant's
    own `scheduler_enabled` and of the tenant activity gate (see
    tenant_eligibility.alerts_allowed); a tenant without any opt-in costs one
    small read per tick."""
    if not SCHEDULER_OPERATOR_CONFIG.alerts_job_enabled:
        return
    from .alerts import runner as alert_runner  # lazy: alerts import actions/esi_data (see _check_and_run_due_jobs_for_tenant)

    for tenant_id, _name, _created_at in storage.list_tenants():
        tenant_id = str(tenant_id)
        try:
            with tenant_scope.enter_tenant(tenant_id):
                if not tenant_eligibility.alerts_allowed(tenant_id) or not alert_runner.enabled_subscriptions():
                    continue
                _run_job(tenant_id, "alerts", alert_runner.run_for_tenant)
        except Exception as e:  # noqa: BLE001 - one tenant's failure must not block the others
            log.warning("Alerts check failed for tenant %s: %s", tenant_id, e)


def _check_and_run_due_jobs() -> None:
    master = _master_enabled()
    if master:
        try:
            last_active = storage.list_tenant_last_active()
        except Exception as e:  # noqa: BLE001 - a failed lookup must not switch everyone off
            log.warning("Could not read tenant activity, treating every tenant as active: %s", e)
            last_active = {}
        for tenant_id, _name, _created_at in storage.list_tenants():
            tenant_id = str(tenant_id)
            try:
                with tenant_scope.enter_tenant(tenant_id):
                    _check_and_run_due_jobs_for_tenant(
                        tenant_id, TRADING_CONFIG,
                        active=tenant_eligibility.is_active(tenant_id, last_active),
                        master_enabled=True,
                    )
            except Exception as e:  # noqa: BLE001 - one tenant's failure must not block the others
                log.warning("Per-tenant job check failed for tenant %s: %s", tenant_id, e)

        _check_and_run_backup_job()
        _check_and_run_jita_price_cache_job()

    # The opt-in alerts job runs regardless of `master`, gated only by its own
    # operator switch (docs/DISCORD_ALERTS_HANDOFF.md).
    _check_and_run_alerts_job()


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
    # The thread runs for the master switch (every legacy job) or for the
    # alerts job alone - alerts must work without switching the backup,
    # pipeline and ESI jobs on (docs/SCHEDULER_REWORK_PLAN.md decision 7).
    if not (_master_enabled() or SCHEDULER_OPERATOR_CONFIG.alerts_job_enabled):
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
    DEFAULT_TENANT_ID's (see _check_and_run_backup_job). `esi_data_sync.last_run_at`
    is the newest `esi_freshness.last_success_at` for this tenant (None if
    nothing has ever succeeded), not `_run_job`'s tick timestamp — that job
    runs every five minutes and usually fetches nothing. `esi_data_sync.last_error`
    still comes from `last_run_status`. The three freshness-tier intervals
    are under `tier_interval_hours`; `interval_hours` is null because this
    job has no single cadence.
    """
    tenant_id = storage.get_current_tenant()
    tenant_jobs = last_run_status.get(tenant_id, {}) if tenant_id else {}
    backups = backup.list_backups()
    return {
        "enabled": TRADING_CONFIG.scheduler_enabled,
        "operator": {
            "backup_job_enabled": SCHEDULER_OPERATOR_CONFIG.backup_job_enabled,
            "jita_price_cache_job_enabled": SCHEDULER_OPERATOR_CONFIG.jita_price_cache_job_enabled,
            "alerts_job_enabled": SCHEDULER_OPERATOR_CONFIG.alerts_job_enabled,
            "inactive_tenant_days": SCHEDULER_OPERATOR_CONFIG.inactive_tenant_days,
        },
        "running": _thread is not None and _thread.is_alive(),
        "jobs": {
            "trading_pipeline": {
                "interval_hours": TRADING_CONFIG.trading_pipeline_interval_hours,
                "last_run_at": storage.get_esi_sync_time("trading"),
                "last_error": tenant_jobs.get("trading_pipeline", {}).get("error"),
            },
            "esi_data_sync": {
                "interval_hours": None,
                "tier_interval_hours": {
                    "frequent": TRADING_CONFIG.esi_frequent_interval_hours,
                    "normal": TRADING_CONFIG.esi_normal_interval_hours,
                    "rare": TRADING_CONFIG.esi_rare_interval_hours,
                },
                "last_run_at": storage.newest_esi_freshness_success_at(),
                "last_error": tenant_jobs.get("esi_data_sync", {}).get("error"),
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

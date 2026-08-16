"""Lightweight in-process background scheduler - a single daemon thread that
wakes up periodically and runs whichever registered job is due, based on each
job's own last-run timestamp. Two of the three jobs reuse storage.
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
loop covers this without a new dependency. Off by default
(TradingConfig.scheduler_enabled) - opt-in, since this is a credentials-
handling tool making its own ESI calls in the background, which should never
start happening without the user explicitly asking for it. (The backup job
itself makes no ESI calls, but it's gated by the same flag rather than a
separate one - simpler than a second on/off switch, and a manual "Backup
Now" button, unaffected by this flag, is always available regardless.)
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

from . import backup, storage
from .config import TRADING_CONFIG, TradingConfig

log = logging.getLogger("eve_trader.scheduler")

CHECK_INTERVAL_SECONDS = 300  # how often the background thread wakes up to check what's due

_thread: threading.Thread | None = None
_stop_event = threading.Event()

# {job_name: {"ran_at": iso str, "error": str | None}} - last outcome of each
# job's most recent run (whether triggered by the scheduler or not, for the
# "ran_at" part - see get_status), surfaced via the portfolio router for a
# small status readout in the UI.
last_run_status: dict[str, dict] = {}


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


def _run_job(name: str, fn) -> None:
    try:
        fn()
        last_run_status[name] = {"ran_at": dt.datetime.now(dt.timezone.utc).isoformat(), "error": None}
    except Exception as e:  # noqa: BLE001 - a job's own failure must never kill the scheduler thread
        log.warning("Scheduled job %r failed: %s", name, e)
        last_run_status[name] = {"ran_at": dt.datetime.now(dt.timezone.utc).isoformat(), "error": str(e)}


def _check_and_run_due_jobs(cfg: TradingConfig) -> None:
    # Lazy imports: actions.py and production/actions.py both import from
    # this same config module - importing them at module load time would
    # risk a circular import; deferring to call time avoids that without
    # restructuring either actions module.
    from . import actions
    from .production import actions as production_actions

    if _hours_since(storage.get_esi_sync_time("trading")) >= cfg.trading_pipeline_interval_hours:
        _run_job("trading_pipeline", lambda: actions.do_pipeline(safe=True))

    if _hours_since(storage.get_esi_sync_time("production")) >= cfg.production_sync_interval_hours:
        _run_job("production_sync", production_actions.do_sync_esi)

    if _hours_since_last_backup() >= cfg.backup_interval_hours:
        _run_job("backup", backup.create_backup)


def _loop(cfg: TradingConfig) -> None:
    while not _stop_event.is_set():
        try:
            _check_and_run_due_jobs(cfg)
        except Exception as e:  # noqa: BLE001 - one bad check must not end the background thread
            log.warning("Scheduler check failed: %s", e)
        _stop_event.wait(CHECK_INTERVAL_SECONDS)


def start(cfg: TradingConfig = TRADING_CONFIG) -> None:
    """Starts the background thread if cfg.scheduler_enabled and not already
    running. Safe to call more than once - only the first call (while no
    thread is alive) actually starts anything, so app.py's startup hook
    doesn't need to track whether it already ran."""
    global _thread
    if not cfg.scheduler_enabled or (_thread is not None and _thread.is_alive()):
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, args=(cfg,), daemon=True, name="eve-trader-scheduler")
    _thread.start()


def stop() -> None:
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)


def get_status(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Read-only snapshot for the UI: whether the background thread is
    enabled/running, plus each job's configured interval and last outcome -
    "last_run_at" comes from storage/the backup directory itself (so it
    reflects a manual run too, not just scheduler-triggered ones),
    "last_error" only from an actual scheduler-triggered attempt (a manual
    run's own error already surfaces directly in the UI at the time it
    happens)."""
    backups = backup.list_backups()
    return {
        "enabled": cfg.scheduler_enabled,
        "running": _thread is not None and _thread.is_alive(),
        "jobs": {
            "trading_pipeline": {
                "interval_hours": cfg.trading_pipeline_interval_hours,
                "last_run_at": storage.get_esi_sync_time("trading"),
                "last_error": last_run_status.get("trading_pipeline", {}).get("error"),
            },
            "production_sync": {
                "interval_hours": cfg.production_sync_interval_hours,
                "last_run_at": storage.get_esi_sync_time("production"),
                "last_error": last_run_status.get("production_sync", {}).get("error"),
            },
            "backup": {
                "interval_hours": cfg.backup_interval_hours,
                "last_run_at": backups[0]["created_at"] if backups else None,
                "last_error": last_run_status.get("backup", {}).get("error"),
            },
        },
    }

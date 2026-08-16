import datetime as dt

from eve_trader import actions, backup, scheduler, storage
from eve_trader.config import TradingConfig
from eve_trader.production import actions as production_actions


def test_hours_since_none_is_infinite():
    assert scheduler._hours_since(None) == float("inf")


def test_hours_since_computes_elapsed_hours():
    two_hours_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    assert round(scheduler._hours_since(two_hours_ago), 1) == 2.0


def test_hours_since_handles_naive_timestamp():
    # actions.now_ts() (what do_pipeline's do_refresh_and_prune_candidates
    # writes to esi_sync_state for scope "trading") is dt.datetime.utcnow() -
    # naive, but already UTC, not local time - must be interpreted as UTC,
    # not raise, and not silently double-offset.
    naive_utc = (dt.datetime.utcnow() - dt.timedelta(hours=1)).isoformat()
    assert round(scheduler._hours_since(naive_utc), 1) == 1.0


def test_run_job_records_success(monkeypatch):
    scheduler.last_run_status.clear()
    scheduler._run_job("fake_job", lambda: None)
    assert scheduler.last_run_status["fake_job"]["error"] is None
    assert scheduler.last_run_status["fake_job"]["ran_at"] is not None


def test_run_job_records_error_without_raising(monkeypatch):
    scheduler.last_run_status.clear()

    def boom():
        raise RuntimeError("network is down")

    scheduler._run_job("fake_job", boom)  # must not raise

    assert scheduler.last_run_status["fake_job"]["error"] == "network is down"


def _recent_backup():
    # A backup "just now" - keeps the backup job out of the way for tests
    # that only care about the trading/production jobs, same reasoning as
    # get_esi_sync_time returning a fresh timestamp for those two.
    return [{"name": "eve_trader_backup_test.zip",
             "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "size_bytes": 1}]


def test_check_and_run_due_jobs_runs_when_never_synced(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    monkeypatch.setattr(backup, "list_backups", _recent_backup)
    calls = []
    monkeypatch.setattr(actions, "do_pipeline", lambda safe=True: calls.append("trading"))
    monkeypatch.setattr(production_actions, "do_sync_esi", lambda: calls.append("production"))

    cfg = TradingConfig(trading_pipeline_interval_hours=24.0, production_sync_interval_hours=6.0)
    scheduler._check_and_run_due_jobs(cfg)

    assert set(calls) == {"trading", "production"}


def test_check_and_run_due_jobs_skips_when_recently_run(monkeypatch):
    just_now = dt.datetime.now(dt.timezone.utc).isoformat()
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: just_now)
    monkeypatch.setattr(backup, "list_backups", _recent_backup)
    calls = []
    monkeypatch.setattr(actions, "do_pipeline", lambda safe=True: calls.append("trading"))
    monkeypatch.setattr(production_actions, "do_sync_esi", lambda: calls.append("production"))

    cfg = TradingConfig(trading_pipeline_interval_hours=24.0, production_sync_interval_hours=6.0)
    scheduler._check_and_run_due_jobs(cfg)

    assert calls == []


def test_check_and_run_due_jobs_one_job_failing_does_not_block_the_others(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    monkeypatch.setattr(backup, "list_backups", _recent_backup)
    calls = []

    def failing_pipeline(safe=True):
        raise RuntimeError("no auth")

    monkeypatch.setattr(actions, "do_pipeline", failing_pipeline)
    monkeypatch.setattr(production_actions, "do_sync_esi", lambda: calls.append("production"))

    cfg = TradingConfig(trading_pipeline_interval_hours=24.0, production_sync_interval_hours=6.0)
    scheduler._check_and_run_due_jobs(cfg)  # must not raise

    assert calls == ["production"]


def test_hours_since_last_backup_is_infinite_with_no_backups(monkeypatch):
    monkeypatch.setattr(backup, "list_backups", lambda: [])
    assert scheduler._hours_since_last_backup() == float("inf")


def test_hours_since_last_backup_uses_newest_entry(monkeypatch):
    two_hours_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    monkeypatch.setattr(backup, "list_backups", lambda: [
        {"name": "b", "created_at": two_hours_ago, "size_bytes": 1},  # newest-first order, as the real function returns
    ])
    assert round(scheduler._hours_since_last_backup(), 1) == 2.0


def test_check_and_run_due_jobs_runs_backup_when_overdue(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: dt.datetime.now(dt.timezone.utc).isoformat())
    monkeypatch.setattr(backup, "list_backups", lambda: [])  # never backed up -> always due
    calls = []
    monkeypatch.setattr(backup, "create_backup", lambda: calls.append("backup"))

    cfg = TradingConfig(trading_pipeline_interval_hours=24.0, production_sync_interval_hours=6.0,
                         backup_interval_hours=24.0)
    scheduler._check_and_run_due_jobs(cfg)

    assert calls == ["backup"]


def test_check_and_run_due_jobs_skips_backup_when_recent(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: dt.datetime.now(dt.timezone.utc).isoformat())
    monkeypatch.setattr(backup, "list_backups", _recent_backup)
    calls = []
    monkeypatch.setattr(backup, "create_backup", lambda: calls.append("backup"))

    cfg = TradingConfig(trading_pipeline_interval_hours=24.0, production_sync_interval_hours=6.0,
                         backup_interval_hours=24.0)
    scheduler._check_and_run_due_jobs(cfg)

    assert calls == []


def test_start_is_noop_when_disabled():
    cfg = TradingConfig(scheduler_enabled=False)
    scheduler.start(cfg)
    assert scheduler._thread is None


def test_get_status_reflects_disabled_config(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    monkeypatch.setattr(backup, "list_backups", lambda: [])
    cfg = TradingConfig(scheduler_enabled=False, trading_pipeline_interval_hours=12.0)

    status = scheduler.get_status(cfg)

    assert status["enabled"] is False
    assert status["jobs"]["trading_pipeline"]["interval_hours"] == 12.0
    assert status["jobs"]["trading_pipeline"]["last_run_at"] is None
    assert status["jobs"]["backup"]["last_run_at"] is None


def test_get_status_reports_last_backup_time(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    monkeypatch.setattr(backup, "list_backups", lambda: [{"name": "b", "created_at": ts, "size_bytes": 1}])
    cfg = TradingConfig()

    status = scheduler.get_status(cfg)

    assert status["jobs"]["backup"]["last_run_at"] == ts

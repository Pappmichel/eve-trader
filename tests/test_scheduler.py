import datetime as dt
from contextlib import contextmanager

import pytest

from eve_trader import actions, backup, config, scheduler, storage
from eve_trader.config import TradingConfig
from eve_trader.doctrine import actions as doctrine_actions
from eve_trader.production import actions as production_actions

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, tenant_pair  # noqa: F401


def _fake_enter_tenant(cfg: TradingConfig):
    """A stand-in for tenant_scope.enter_tenant that only resolves
    TRADING_CONFIG (via a direct ContextVar.set, not real Postgres) - used
    by tests that only care about "does this function honor TRADING_CONFIG's
    value", not the resolution mechanism itself (covered separately by
    tests/test_tenant_scope.py)."""
    @contextmanager
    def _enter(tenant_id):
        token = config._trading_config_var.set(cfg)
        try:
            yield
        finally:
            config._trading_config_var.reset(token)
    return _enter


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


def test_run_job_records_success_under_its_tenant():
    scheduler.last_run_status.clear()
    scheduler._run_job("test-tenant", "fake_job", lambda: None)
    assert scheduler.last_run_status["test-tenant"]["fake_job"]["error"] is None
    assert scheduler.last_run_status["test-tenant"]["fake_job"]["ran_at"] is not None


def test_run_job_records_error_without_raising():
    scheduler.last_run_status.clear()

    def boom():
        raise RuntimeError("network is down")

    scheduler._run_job("test-tenant", "fake_job", boom)  # must not raise

    assert scheduler.last_run_status["test-tenant"]["fake_job"]["error"] == "network is down"


def test_run_job_with_no_tenant_records_into_global_backup_status():
    # tenant_id=None - only the (global, unscoped) backup job ever does this.
    scheduler._backup_status.clear()
    scheduler._run_job(None, "backup", lambda: None)
    assert scheduler._backup_status["error"] is None


def _recent_backup():
    # A backup "just now" - keeps the backup job out of the way for tests
    # that only care about the trading/production jobs, same reasoning as
    # get_esi_sync_time returning a fresh timestamp for those two.
    return [{"name": "eve_trader_backup_test.zip",
             "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "size_bytes": 1}]


def test_check_and_run_due_jobs_for_tenant_runs_when_never_synced(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    calls = []
    monkeypatch.setattr(actions, "do_pipeline", lambda safe=True: calls.append("trading"))
    monkeypatch.setattr(production_actions, "do_sync_esi", lambda: calls.append("production"))
    monkeypatch.setattr(doctrine_actions, "do_sync_contracts", lambda: calls.append("doctrine"))

    cfg = TradingConfig(scheduler_enabled=True, trading_pipeline_interval_hours=24.0, production_sync_interval_hours=6.0,
                         doctrine_sync_interval_hours=12.0)
    scheduler._check_and_run_due_jobs_for_tenant("test-tenant", cfg)

    assert set(calls) == {"trading", "production", "doctrine"}


def test_check_and_run_due_jobs_for_tenant_skips_when_recently_run(monkeypatch):
    just_now = dt.datetime.now(dt.timezone.utc).isoformat()
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: just_now)
    calls = []
    monkeypatch.setattr(actions, "do_pipeline", lambda safe=True: calls.append("trading"))
    monkeypatch.setattr(production_actions, "do_sync_esi", lambda: calls.append("production"))
    monkeypatch.setattr(doctrine_actions, "do_sync_contracts", lambda: calls.append("doctrine"))

    cfg = TradingConfig(trading_pipeline_interval_hours=24.0, production_sync_interval_hours=6.0,
                         doctrine_sync_interval_hours=12.0)
    scheduler._check_and_run_due_jobs_for_tenant("test-tenant", cfg)

    assert calls == []


def test_check_and_run_due_jobs_for_tenant_skips_entirely_when_disabled(monkeypatch):
    # A tenant's own scheduler_enabled=False must skip every job, even if
    # all are otherwise "due" - independent of any other tenant's setting.
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    calls = []
    monkeypatch.setattr(actions, "do_pipeline", lambda safe=True: calls.append("trading"))
    monkeypatch.setattr(production_actions, "do_sync_esi", lambda: calls.append("production"))
    monkeypatch.setattr(doctrine_actions, "do_sync_contracts", lambda: calls.append("doctrine"))

    cfg = TradingConfig(scheduler_enabled=False, trading_pipeline_interval_hours=24.0,
                         production_sync_interval_hours=6.0, doctrine_sync_interval_hours=12.0)
    scheduler._check_and_run_due_jobs_for_tenant("test-tenant", cfg)

    assert calls == []


def test_check_and_run_due_jobs_for_tenant_one_job_failing_does_not_block_the_others(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    calls = []

    def failing_pipeline(safe=True):
        raise RuntimeError("no auth")

    monkeypatch.setattr(actions, "do_pipeline", failing_pipeline)
    monkeypatch.setattr(production_actions, "do_sync_esi", lambda: calls.append("production"))
    monkeypatch.setattr(doctrine_actions, "do_sync_contracts", lambda: calls.append("doctrine"))

    cfg = TradingConfig(scheduler_enabled=True, trading_pipeline_interval_hours=24.0, production_sync_interval_hours=6.0,
                         doctrine_sync_interval_hours=12.0)
    scheduler._check_and_run_due_jobs_for_tenant("test-tenant", cfg)  # must not raise

    assert set(calls) == {"production", "doctrine"}


def test_hours_since_last_backup_is_infinite_with_no_backups(monkeypatch):
    monkeypatch.setattr(backup, "list_backups", lambda: [])
    assert scheduler._hours_since_last_backup() == float("inf")


def test_hours_since_last_backup_uses_newest_entry(monkeypatch):
    two_hours_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    monkeypatch.setattr(backup, "list_backups", lambda: [
        {"name": "b", "created_at": two_hours_ago, "size_bytes": 1},  # newest-first order, as the real function returns
    ])
    assert round(scheduler._hours_since_last_backup(), 1) == 2.0


def test_check_and_run_backup_job_runs_when_overdue(monkeypatch):
    monkeypatch.setattr(backup, "list_backups", lambda: [])  # never backed up -> always due
    calls = []
    monkeypatch.setattr(backup, "create_backup", lambda: calls.append("backup"))
    monkeypatch.setattr(scheduler.tenant_scope, "enter_tenant",
                         _fake_enter_tenant(TradingConfig(backup_interval_hours=24.0)))

    scheduler._check_and_run_backup_job()

    assert calls == ["backup"]


def test_check_and_run_backup_job_skips_when_recent(monkeypatch):
    monkeypatch.setattr(backup, "list_backups", _recent_backup)
    calls = []
    monkeypatch.setattr(backup, "create_backup", lambda: calls.append("backup"))
    monkeypatch.setattr(scheduler.tenant_scope, "enter_tenant",
                         _fake_enter_tenant(TradingConfig(backup_interval_hours=24.0)))

    scheduler._check_and_run_backup_job()

    assert calls == []


def test_start_is_noop_when_default_tenant_disabled(monkeypatch):
    monkeypatch.setattr(scheduler.tenant_scope, "enter_tenant",
                         _fake_enter_tenant(TradingConfig(scheduler_enabled=False)))
    scheduler._thread = None
    scheduler.start()
    assert scheduler._thread is None


def test_get_status_reflects_disabled_config(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    monkeypatch.setattr(backup, "list_backups", lambda: [])
    token = config._trading_config_var.set(TradingConfig(scheduler_enabled=False, trading_pipeline_interval_hours=12.0))
    try:
        status = scheduler.get_status()
    finally:
        config._trading_config_var.reset(token)

    assert status["enabled"] is False
    assert status["jobs"]["trading_pipeline"]["interval_hours"] == 12.0
    assert status["jobs"]["trading_pipeline"]["last_run_at"] is None
    assert status["jobs"]["backup"]["last_run_at"] is None


def test_get_status_reports_last_backup_time(monkeypatch):
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    monkeypatch.setattr(backup, "list_backups", lambda: [{"name": "b", "created_at": ts, "size_bytes": 1}])

    status = scheduler.get_status()

    assert status["jobs"]["backup"]["last_run_at"] == ts


# --------------------------------------------------- real Postgres, per-tenant iteration
@pg_helpers.postgres_required()
def test_check_and_run_due_jobs_iterates_tenants_independently(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, tenant_pair
):
    # The actual multi-tenant behavior this phase adds: two tenants, one
    # with scheduler_enabled saved True, the other False (via real
    # tenant_settings) - only the enabled one's job must run, proving
    # storage.list_tenants() + tenant_scope.enter_tenant resolve each
    # tenant's own config independently, not a shared one.
    tenant_a, tenant_b = tenant_pair
    monkeypatch.setattr(storage, "list_tenants", lambda: [(tenant_a, "A", None), (tenant_b, "B", None)])
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: None)  # always due
    monkeypatch.setattr(backup, "list_backups", lambda: [{"name": "b", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "size_bytes": 1}])  # backup not due
    calls = []
    monkeypatch.setattr(actions, "do_pipeline", lambda safe=True: calls.append(storage.get_current_tenant()))
    monkeypatch.setattr(production_actions, "do_sync_esi", lambda: None)
    monkeypatch.setattr(doctrine_actions, "do_sync_contracts", lambda: None)

    with storage.tenant_context(tenant_a):
        storage.save_tenant_settings("trading", {"scheduler_enabled": True, "trading_pipeline_interval_hours": 24.0})
    with storage.tenant_context(tenant_b):
        storage.save_tenant_settings("trading", {"scheduler_enabled": False})

    scheduler._check_and_run_due_jobs()

    assert calls == [tenant_a]
    assert tenant_a in scheduler.last_run_status
    assert tenant_b not in scheduler.last_run_status

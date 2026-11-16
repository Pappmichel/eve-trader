"""Retention of shortlist_snapshot (per tenant) and goonmetrics_history
(global): chunked prunes, the operator field, the scheduler gate, the index."""
import datetime as dt

import pytest

from eve_trader import scheduler, storage
from eve_trader.config import ConfigError, TradingConfig, validate_config_overrides

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant, tenant_pair  # noqa: F401

pytestmark = pg_helpers.postgres_required()


def _insert_runs(runs: dict[str, int]) -> None:
    with storage.connect() as conn:
        for run_ts, n in runs.items():
            conn.executemany(
                "INSERT INTO shortlist_snapshot (run_ts, item_id) VALUES (?, ?)",
                [(run_ts, i) for i in range(n)],
            )


def _runs() -> dict[str, int]:
    with storage.connect() as conn:
        return dict(conn.execute("SELECT run_ts, count(*) FROM shortlist_snapshot GROUP BY run_ts").fetchall())


def test_prune_shortlist_snapshot_deletes_old_runs_keeps_newest(tenant):
    _insert_runs({"2026-01-01T00:00:00": 5, "2026-06-01T00:00:00": 5, "2026-11-01T00:00:00": 5})
    deleted = storage.prune_shortlist_snapshot("2026-09-01T00:00:00")
    assert deleted == 10
    assert _runs() == {"2026-11-01T00:00:00": 5}


def test_prune_shortlist_snapshot_keeps_newest_run_even_when_old(tenant):
    _insert_runs({"2026-01-01T00:00:00": 3, "2026-02-01T00:00:00": 4})
    storage.prune_shortlist_snapshot("2026-09-01T00:00:00")
    assert _runs() == {"2026-02-01T00:00:00": 4}


def test_prune_shortlist_snapshot_runs_in_chunks(tenant):
    _insert_runs({"2026-01-01T00:00:00": 23, "2026-11-01T00:00:00": 2})
    assert storage.prune_shortlist_snapshot("2026-09-01T00:00:00", chunk_rows=5) == 23
    assert _runs() == {"2026-11-01T00:00:00": 2}


def test_prune_shortlist_snapshot_leaves_other_tenants_alone(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        _insert_runs({"2026-01-01T00:00:00": 3, "2026-11-01T00:00:00": 1})
    with storage.tenant_context(b):
        _insert_runs({"2026-01-01T00:00:00": 3, "2026-02-01T00:00:00": 1})
    with storage.tenant_context(a):
        storage.prune_shortlist_snapshot("2026-09-01T00:00:00")
        assert _runs() == {"2026-11-01T00:00:00": 1}
    with storage.tenant_context(b):
        assert _runs() == {"2026-01-01T00:00:00": 3, "2026-02-01T00:00:00": 1}


def test_prune_goonmetrics_history_by_date(tenant):
    region = 90_000_000 + int(dt.datetime.now().timestamp()) % 1_000_000  # no collision with other rows
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO goonmetrics_history VALUES (?,?,?,?,?,?,?,?)",
            [(region, 1, d, 1.0, 2.0, 1.5, 10.0, 3) for d in ("2026-01-01", "2026-08-31", "2026-09-01", "2026-11-01")],
        )
    deleted = storage.prune_goonmetrics_history("2026-09-01", chunk_rows=1)
    with storage.connect() as conn:
        left = [r[0] for r in conn.execute(
            "SELECT date FROM goonmetrics_history WHERE region_id = ? ORDER BY date", (region,)).fetchall()]
        conn.execute("DELETE FROM goonmetrics_history WHERE region_id = ?", (region,))
    assert left == ["2026-09-01", "2026-11-01"]
    assert deleted >= 2


def test_history_retention_days_has_a_floor_of_30():
    assert TradingConfig().history_retention_days == 90.0
    validate_config_overrides(TradingConfig(), {"history_retention_days": 30})
    with pytest.raises(ConfigError):
        validate_config_overrides(TradingConfig(), {"history_retention_days": 29})


def test_snapshot_tenant_run_index_exists():
    with storage.connect_unscoped() as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_shortlist_snapshot_tenant_run'"
        ).fetchone()
    assert row is not None


# ------------------------------------------------------------ scheduler gate
@pytest.fixture
def _clean_status():
    scheduler.last_run_status.clear()
    scheduler._goonmetrics_retention_status.clear()
    with scheduler._job_mu:
        scheduler._running_jobs.clear()
    yield
    scheduler.last_run_status.clear()
    scheduler._goonmetrics_retention_status.clear()


def _tenant_pass(monkeypatch, grants, cfg=None):
    from eve_trader.esi_data import orchestrator as esi_orchestrator
    from eve_trader.asset_manager import actions as asset_manager_actions
    monkeypatch.setattr(esi_orchestrator, "pending_due", lambda **kw: [])
    monkeypatch.setattr(asset_manager_actions, "history_snapshot_due", lambda: False)
    monkeypatch.setattr(scheduler.portfolio, "_quotes_available", lambda: False)
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: "2999-01-01T00:00:00+00:00")
    monkeypatch.setattr(storage, "latest_portfolio_snapshot_date", lambda: scheduler.timeutil.utc_today())
    monkeypatch.setattr(scheduler.tenant_eligibility, "granted_tools", lambda t: grants)
    calls = []
    monkeypatch.setattr(scheduler, "_prune_shortlist_snapshot_job", lambda: calls.append("prune"))
    scheduler._check_and_run_due_jobs_for_tenant("t1", cfg or TradingConfig(scheduler_enabled=True))
    return calls


def test_shortlist_retention_runs_once_a_day(monkeypatch, _clean_status):
    assert _tenant_pass(monkeypatch, None) == ["prune"]
    assert _tenant_pass(monkeypatch, None) == []


def test_shortlist_retention_needs_master_switch_and_trading_grant(monkeypatch, _clean_status):
    assert _tenant_pass(monkeypatch, {"production"}) == []
    assert _tenant_pass(monkeypatch, None, TradingConfig(scheduler_enabled=False)) == []


def test_shortlist_retention_retries_after_backoff_only(monkeypatch, _clean_status):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(scheduler, "_prune_shortlist_snapshot_job", boom)
    scheduler._run_job("t1", "shortlist_retention", boom)
    assert scheduler._retention_due("t1", "shortlist_retention") is False  # failed just now: backoff


def test_goonmetrics_retention_global_job(monkeypatch, _clean_status):
    calls = []
    monkeypatch.setattr(scheduler, "_prune_goonmetrics_history_job", lambda: calls.append(1))

    class _Ctx:
        def __enter__(self): return None
        def __exit__(self, *a): return False
    monkeypatch.setattr(scheduler.tenant_scope, "enter_tenant", lambda t: _Ctx())
    scheduler._check_and_run_goonmetrics_retention_job()
    scheduler._check_and_run_goonmetrics_retention_job()
    assert calls == [1]
    assert "ran_at" in scheduler._goonmetrics_retention_status

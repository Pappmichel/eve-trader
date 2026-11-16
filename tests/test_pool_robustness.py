"""Token pre-refresh before a batch session, context-carrying tenant wrapper,
transient-failure backoff, atomic backup file and one-solver-at-a-time."""
from __future__ import annotations

import os
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
import requests

from eve_trader import backup, config, mineral_supply_lp, storage, tenant_scope
from eve_trader.auth import TokenManager, TokenRecord
from eve_trader.config import TRADING_CONFIG
from eve_trader.esi_client import ESIError, ESIRateLimited, ESIStatusError
from eve_trader.esi_data import orchestrator

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)
from .test_esi_orchestrator import ASSETS_SCOPE, FakeClient, _asset, _share

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("character_assets", "esi_sharing", "esi_freshness", "tenant_tokens")
    yield
    orchestrator._in_flight.clear()


@pytest.fixture(autouse=True)
def _no_resolver(monkeypatch):
    monkeypatch.setattr(orchestrator, "_resolve_locations", lambda: {"resolved": 0})


# ------------------------------------------------------------------- Fix 1
class _SsoResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"access_token": "fresh", "refresh_token": "next", "expires_in": 1200}


def _expired_owner(cid: int) -> None:
    role = f"producer:{cid}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=cid, character_name=f"C{cid}", access_token="old",
        refresh_token="r", expires_at=0.0, scopes=ASSETS_SCOPE,
    )))
    _share("character", cid, "assets", "production")


def _run_owner(tm, client, cid):
    return orchestrator._run_character_owner(
        client=client, owner_id=cid, kinds=["assets"],
        characters=tm.list_records(), extra={}, tokens=tm,
    )


def test_ten_parallel_owners_with_expired_tokens_do_not_exhaust_the_pool(tenant, monkeypatch):
    from psycopg_pool import ConnectionPool
    ids = list(range(5001, 5011))
    for cid in ids:
        _expired_owner(cid)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _SsoResp())
    small = ConnectionPool(storage.PG_DSN, min_size=1, max_size=10, timeout=4, open=True)
    monkeypatch.setattr(storage, "_pool", small)
    try:
        tm = TokenManager()
        client = FakeClient(assets={cid: [_asset(cid, cid)] for cid in ids})
        wrapped = storage.with_current_tenant(lambda cid: _run_owner(tm, client, cid))
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(wrapped, ids))
    finally:
        monkeypatch.setattr(storage, "_pool", None)
        small.close()
    assert all(r["ok"] for r in results), [r.get("error") for r in results if not r["ok"]]


def test_refresh_runs_while_the_thread_holds_no_batch_connection(tenant, monkeypatch):
    _expired_owner(5101)
    held = []

    def sso(*a, **k):
        held.append(getattr(storage._batch_local, "conn", None) is not None)
        return _SsoResp()

    monkeypatch.setattr(requests, "post", sso)
    tm = TokenManager()
    result = _run_owner(tm, FakeClient(assets={5101: [_asset(1, 5101)]}), 5101)
    assert result["ok"] is True
    assert held == [False]


def test_failed_pre_refresh_does_not_stop_the_owner_and_reports_as_before(tenant, monkeypatch):
    _expired_owner(5201)

    def dead(url, **k):
        resp = requests.Response()
        resp.status_code = 400
        resp.url = url
        resp._content = b"{}"
        return resp

    monkeypatch.setattr(requests, "post", dead)
    tm = TokenManager()

    class _TokenClient(FakeClient):
        def character_assets(self, character_id, auth_role):
            try:
                tm.get_token(auth_role)          # the lazy refresh, as the real client does
            except requests.HTTPError as e:
                raise ESIError(f"token: {e}") from e
            return super().character_assets(character_id, auth_role)

    result = _run_owner(tm, _TokenClient(assets={5201: [_asset(1, 5201)]}), 5201)
    assert result["ok"] is False
    assert "assets" in result["kinds"]


def test_token_save_retries_a_busy_pool_and_keeps_the_record(tenant, monkeypatch):
    from psycopg_pool import PoolTimeout
    _expired_owner(5301)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _SsoResp())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    real = storage.save_tenant_token
    calls = {"n": 0}

    def flaky(role, rec):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PoolTimeout("busy")
        return real(role, rec)

    monkeypatch.setattr(storage, "save_tenant_token", flaky)
    tm = TokenManager()
    rec = tm.ensure_fresh("producer:5301")
    assert rec.access_token == "fresh" and calls["n"] == 3
    assert TokenManager().get_record("producer:5301").access_token == "fresh"


def test_token_save_gives_up_after_three_attempts_but_keeps_the_record(tenant, monkeypatch):
    from psycopg_pool import PoolTimeout
    _expired_owner(5401)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _SsoResp())
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def boom(role, rec):
        raise PoolTimeout("busy")

    monkeypatch.setattr(storage, "save_tenant_token", boom)
    tm = TokenManager()
    rec = tm.ensure_fresh("producer:5401")
    assert rec.access_token == "fresh"
    assert tm._tokens["producer:5401"].refresh_token == "next"


def test_ensure_fresh_refreshes_a_token_that_is_about_to_expire(tenant, monkeypatch):
    role = "producer:5501"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=5501, character_name="C", access_token="old",
        refresh_token="r", expires_at=time.time() + 120, scopes=ASSETS_SCOPE)))
    monkeypatch.setattr(requests, "post", lambda *a, **k: _SsoResp())
    tm = TokenManager()
    assert tm.get_token(role).access_token == "old"        # still valid for get_token
    assert tm.ensure_fresh(role).access_token == "fresh"   # < 300 s left


# ------------------------------------------------------------------- Fix 2
def test_with_current_tenant_carries_the_tenant_config(tenant):
    from eve_trader.config import TradingConfig
    cfg = TradingConfig()
    cfg.jita_region_id = 10000043
    cfg.chunk_size = 7
    token = config._trading_config_var.set(cfg)
    try:
        fn = storage.with_current_tenant(
            lambda: (TRADING_CONFIG.jita_region_id, TRADING_CONFIG.chunk_size))
    finally:
        config._trading_config_var.reset(token)
    # outside that scope the wrapper still sees the snapshot, in a worker thread
    with ThreadPoolExecutor(max_workers=2) as pool:
        seen = [f.result() for f in [pool.submit(fn), pool.submit(fn)]]
    assert seen == [(10000043, 7), (10000043, 7)]


def test_with_current_tenant_carries_an_entered_tenant_scope(tenant):
    with tenant_scope.enter_tenant(tenant):
        expected = TRADING_CONFIG.jita_region_id
        fn = storage.with_current_tenant(lambda: (storage.get_current_tenant(), TRADING_CONFIG.jita_region_id))
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(fn).result() == (tenant, expected)


def test_same_wrapper_can_run_in_two_threads_at_once(tenant):
    barrier = threading.Barrier(2, timeout=5)

    def work():
        barrier.wait()
        return storage.get_current_tenant()

    fn = storage.with_current_tenant(work)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(fn), pool.submit(fn)]]
    assert results == [tenant, tenant]


def test_without_a_tenant_the_wrapped_call_still_fails_closed():
    def use():
        with storage.connect():
            pass

    fn = storage.with_current_tenant(use)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError):
            pool.submit(fn).result()


# ------------------------------------------------------------------- Fix 4
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _fail(error, at=T0, kind="wallet_balance"):
    storage.upsert_esi_freshness(
        "character", 1, kind, success=False, error=str(error), now=at.isoformat(),
        transient=orchestrator._is_transient_error(error),
    )


def _due(minutes, kind="wallet_balance"):
    return orchestrator._kind_is_due(
        "character", 1, kind, now=T0 + timedelta(minutes=minutes),
        freshness=orchestrator._freshness_map(),
    )


def test_transient_failure_retries_after_15_minutes(tenant):
    _fail(ESIStatusError(503, "HTTP 503"))
    assert _due(14) is False
    assert _due(16) is True


def test_second_and_third_transient_failure_back_off_30_and_60_minutes(tenant):
    err = ESIStatusError(502, "HTTP 502")
    _fail(err)
    _fail(err)
    assert (_due(29), _due(31)) == (False, True)
    _fail(err)
    assert (_due(59), _due(61)) == (False, True)


def test_success_resets_the_failure_count(tenant):
    err = ESIRateLimited(30, "g")
    _fail(err)
    _fail(err)
    assert storage.list_esi_failure_counts() == {("character", 1, "wallet_balance"): 2}
    storage.upsert_esi_freshness("character", 1, "wallet_balance", success=True, now=T0.isoformat())
    assert storage.list_esi_failure_counts() == {}


def test_dead_owner_and_reauth_keep_the_long_backoff(tenant):
    tier = orchestrator._tier_hours("assets")
    long_backoff = min(tier, orchestrator.FAILURE_BACKOFF_HOURS)
    _fail(ESIStatusError(403, "HTTP 403"), kind="assets")
    assert _due(long_backoff * 60 - 1, "assets") is False
    storage.record_esi_attempt("character", 1, "assets", now=T0.isoformat())
    assert _due(30, "assets") is False


def test_suspicious_empty_is_not_transient(tenant):
    err = ESIError("suspicious_empty: ESI returned no assets")
    assert orchestrator._is_transient_error(err) is False
    _fail(err, kind="assets")
    assert _due(16, "assets") is False


def test_short_tier_still_wins_over_the_transient_backoff(tenant, monkeypatch):
    monkeypatch.setattr(TRADING_CONFIG, "esi_normal_interval_hours", 0.1)
    _fail(ESIStatusError(500, "HTTP 500"))
    assert _due(7) is True      # 6 min tier < 15 min


def test_non_esi_errors_are_not_transient():
    assert orchestrator._is_transient_error(RuntimeError("sql")) is False
    assert orchestrator._is_transient_error(ESIError("network error")) is True
    assert orchestrator._is_transient_error(ESIStatusError(404, "HTTP 404")) is False


# ------------------------------------------------------------------- Fix 7
@pytest.fixture
def bdir(tmp_path, monkeypatch):
    d = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", d)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("x: 1\n")
    monkeypatch.setattr(backup, "DEFAULT_CONFIG_PATH", cfg)

    class _R:
        returncode = 0
        stderr = b""

    def run(cmd, stdout=None, stderr=None, timeout=None):
        stdout.write(b"DUMP")
        return _R()

    monkeypatch.setattr(backup.subprocess, "run", run)
    return d


def test_backup_success_leaves_one_zip_and_no_temp_files(bdir):
    backup.create_backup()
    names = sorted(p.name for p in bdir.iterdir())
    assert len(names) == 1 and names[0].endswith(".zip")


def test_killed_backup_leaves_nothing_the_list_or_prune_can_see(bdir, monkeypatch):
    class Boom(zipfile.ZipFile):
        def write(self, *a, **k):
            super().write(*a, **k)
            raise KeyboardInterrupt

    monkeypatch.setattr(backup.zipfile, "ZipFile", Boom)
    with pytest.raises(KeyboardInterrupt):
        backup.create_backup()
    assert backup.list_backups() == []
    assert list(bdir.glob("*.zip")) == []


def test_a_partial_file_is_invisible_to_both_families(bdir):
    bdir.mkdir()
    (bdir / f"{backup.BACKUP_NAME_PREFIX}x.zip.partial").write_bytes(b"PKtrunc")
    (bdir / f"{backup.PREDEPLOY_BACKUP_PREFIX}x.zip.partial").write_bytes(b"PK")
    assert backup.list_backups() == []
    backup._prune_old_backups()
    backup._prune_old_backups(backup.PREDEPLOY_BACKUP_PREFIX)
    assert len(list(bdir.glob("*.partial"))) == 2


def test_truncated_archive_is_rejected_by_the_verification(bdir, monkeypatch):
    monkeypatch.setattr(backup.zipfile.ZipFile, "testzip", lambda self: "eve_trader.dump")
    with pytest.raises(backup.BackupError):
        backup.create_backup()
    assert list(bdir.iterdir()) == []


def test_testzip_rejects_a_cut_off_archive(tmp_path):
    good = tmp_path / "a.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("x", b"y" * 1000)
    cut = tmp_path / "b.zip.partial"
    cut.write_bytes(good.read_bytes()[:-30])
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(cut).testzip()


def test_cleanup_partial_files_removes_old_and_keeps_young(bdir):
    bdir.mkdir()
    old_tmp = bdir / ".tmp_old.dump"
    old_partial = bdir / "eve_trader_backup_old.zip.partial"
    young = bdir / "eve_trader_backup_young.zip.partial"
    keep_zip = bdir / "eve_trader_backup_ok.zip"
    for p in (old_tmp, old_partial, young, keep_zip):
        p.write_bytes(b"x")
    past = time.time() - 3600
    for p in (old_tmp, old_partial, keep_zip):
        os.utime(p, (past, past))
    assert backup.cleanup_partial_files() == 2
    assert not old_tmp.exists() and not old_partial.exists()
    assert young.exists() and keep_zip.exists()


def test_predeploy_family_is_atomic_too(bdir):
    backup.create_backup(backup.PREDEPLOY_BACKUP_PREFIX)
    assert [p.suffix for p in bdir.iterdir()] == [".zip"]


# ------------------------------------------------------------------- Fix 8
def test_solver_processes_never_overlap(monkeypatch):
    active = {"n": 0, "max": 0}
    guard = threading.Lock()

    class _Done:
        returncode = 1
        stdout = b""
        stderr = b"x"

    def run(*a, **k):
        with guard:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.15)
        with guard:
            active["n"] -= 1
        return _Done()

    monkeypatch.setattr(mineral_supply_lp.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: mineral_supply_lp._solve_mip_in_child({}), range(3)))
    assert active["max"] == 1


def test_a_caller_that_waits_too_long_gets_none(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class _Done:
        returncode = 1
        stdout = b""
        stderr = b""

    def run(*a, **k):
        started.set()
        release.wait(5)
        return _Done()

    monkeypatch.setattr(mineral_supply_lp.subprocess, "run", run)
    monkeypatch.setattr(mineral_supply_lp, "MIP_QUEUE_WAIT_SECONDS", 0.01)
    t = threading.Thread(target=lambda: mineral_supply_lp._solve_mip_in_child({}))
    t.start()
    assert started.wait(5)
    assert mineral_supply_lp._solve_mip_in_child({}) is None
    release.set()
    t.join(5)


@pytest.mark.parametrize("exc", [
    mineral_supply_lp.subprocess.TimeoutExpired("x", 1), OSError("no exec"),
])
def test_the_slot_is_released_after_a_timeout_or_start_failure(monkeypatch, exc):
    def run(*a, **k):
        raise exc

    monkeypatch.setattr(mineral_supply_lp.subprocess, "run", run)
    assert mineral_supply_lp._solve_mip_in_child({}) is None
    assert mineral_supply_lp._mip_slot.acquire(timeout=0.1)
    mineral_supply_lp._mip_slot.release()

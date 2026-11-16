"""No built-in database credentials, explicit table grants, and the
per-statement timeout with its long_running() escape."""
from __future__ import annotations

import contextvars
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from eve_trader import storage
from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------- no default DSN
def test_pool_without_dsn_raises_clear_error(monkeypatch):
    monkeypatch.setattr(storage, "PG_DSN", "")
    monkeypatch.setattr(storage, "_pool", None)
    with pytest.raises(RuntimeError, match="EVE_TRADER_PG_DSN"):
        storage._get_pool()


def test_importing_storage_without_dsn_does_not_raise(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "EVE_TRADER_PG_DSN"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", "import eve_trader.storage"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def test_no_password_default_in_source():
    source = (REPO_ROOT / "eve_trader" / "storage.py").read_text(encoding="utf-8")
    assert "password=" not in source
    schema = (REPO_ROOT / "docs" / "phase1_schema.sql").read_text(encoding="utf-8")
    assert "app_devpassword" not in schema


# ----------------------------------------------------------------- pool options
def test_pool_kwargs_default_and_overrides(monkeypatch):
    dsn = "host=h dbname=d user=u password=p"
    monkeypatch.delenv("EVE_TRADER_STATEMENT_TIMEOUT_MS", raising=False)
    assert storage._pool_kwargs(dsn) == {"options": "-c statement_timeout=60000"}
    monkeypatch.setenv("EVE_TRADER_STATEMENT_TIMEOUT_MS", "1500")
    assert storage._pool_kwargs(dsn) == {"options": "-c statement_timeout=1500"}
    monkeypatch.setenv("EVE_TRADER_STATEMENT_TIMEOUT_MS", "0")
    assert storage._pool_kwargs(dsn) == {}
    monkeypatch.setenv("EVE_TRADER_STATEMENT_TIMEOUT_MS", "garbage")
    assert storage._pool_kwargs(dsn) == {"options": "-c statement_timeout=60000"}


def test_dsn_with_own_options_is_untouched(monkeypatch):
    monkeypatch.delenv("EVE_TRADER_STATEMENT_TIMEOUT_MS", raising=False)
    dsn = "host=h dbname=d user=u options='-c statement_timeout=5000'"
    assert storage._pool_kwargs(dsn) == {}


# ----------------------------------------------------------------- grants
pytestmark_pg = pg_helpers.postgres_required()


@pytestmark_pg
def test_every_public_table_is_selectable_and_error_log_not_updatable():
    with pg_helpers.psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT c.relname, has_table_privilege('eve_trader_app', c.oid, 'SELECT'), "
            "has_table_privilege('eve_trader_app', c.oid, 'UPDATE') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
        ).fetchall()
    assert rows
    missing = sorted(name for name, can_select, _ in rows if not can_select)
    assert not missing, f"tables without SELECT for the app role: {missing}"
    updatable = {name for name, _, can_update in rows if can_update}
    assert "error_log" not in updatable


# ----------------------------------------------------------------- statement timeout
@pytest.fixture
def short_timeout_pool(monkeypatch, tenant):
    monkeypatch.setenv("EVE_TRADER_STATEMENT_TIMEOUT_MS", "100")
    pool = ConnectionPool(
        storage.PG_DSN, min_size=1, max_size=3, open=True,
        kwargs=storage._pool_kwargs(storage.PG_DSN),
    )
    monkeypatch.setattr(storage, "_pool", pool)
    yield pool
    monkeypatch.undo()
    pool.close()


def _sleep(conn):
    conn.execute("SELECT pg_sleep(0.5)")


@pytestmark_pg
def test_normal_connect_is_cancelled(short_timeout_pool):
    with pytest.raises(Exception, match="statement timeout"):
        with storage.connect() as conn:
            _sleep(conn)


@pytestmark_pg
def test_long_running_lifts_timeout_in_every_session_kind(short_timeout_pool):
    with storage.long_running():
        with storage.connect() as conn:
            _sleep(conn)
        with storage.connect_own_transaction() as conn:
            _sleep(conn)
        with storage.batch_session():
            with storage.connect() as conn:
                _sleep(conn)
        with storage.batch_session(isolation="repeatable read"):
            with storage.connect() as conn:
                _sleep(conn)
        with storage.connect_unscoped() as conn:
            _sleep(conn)


@pytestmark_pg
def test_lifted_timeout_does_not_leak_to_the_pooled_connection(short_timeout_pool):
    with storage.long_running():
        with storage.connect() as conn:
            _sleep(conn)
    with pytest.raises(Exception, match="statement timeout"):
        with storage.connect() as conn:
            _sleep(conn)
    with pytest.raises(Exception, match="statement timeout"):
        with storage.batch_session():
            with storage.connect() as conn:
                _sleep(conn)


@pytestmark_pg
def test_batch_session_applies_timeout_without_long_running(short_timeout_pool):
    with pytest.raises(Exception, match="statement timeout"):
        with storage.batch_session(isolation="repeatable read"):
            with storage.connect() as conn:
                _sleep(conn)


@pytestmark_pg
def test_flag_travels_into_worker_threads(short_timeout_pool, tenant):
    results: dict[str, object] = {}

    def work(label):
        try:
            with storage.connect() as conn:
                _sleep(conn)
            results[label] = "ok"
        except Exception as e:  # noqa: BLE001
            results[label] = e

    with storage.long_running():
        ctx = contextvars.copy_context()
        wrapped = storage.with_current_tenant(lambda: work("wrapped"))
        t1 = threading.Thread(target=ctx.run, args=(lambda: work("copied"),))
        t2 = threading.Thread(target=wrapped)
        t1.start(); t2.start(); t1.join(); t2.join()
    assert results == {"copied": "ok", "wrapped": "ok"}
    assert storage._long_running_var.get() is False

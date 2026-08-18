"""Proves storage.py's connect()/batch_session() pooling behavior against the
real Postgres pool - the Postgres-pool equivalent of what this file used to
verify against SQLite's sqlite3.connect() (one real connection per call,
reused across a batch). Counts real pool checkouts (pool.getconn) instead of
sqlite3.connect() calls, since storage.py now checks connections out of a
psycopg_pool.ConnectionPool rather than opening its own each time.

The old `test_batch_session_raises_when_nested_for_a_different_db_path` has
no equivalent here - `db_path` is gone entirely (tenant scoping is ambient,
via a contextvar, not a per-call parameter), so a nested batch_session() is
now unconditionally reentrant; there's no "different db_path" case left to
raise on.
"""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def _count_real_checkouts(monkeypatch):
    """Wraps the pool's getconn so real connection checkouts can be asserted
    on, without changing its actual behavior."""
    calls = []
    pool = storage._get_pool()
    real_getconn = pool.getconn

    def _wrapped(*args, **kwargs):
        calls.append(1)
        return real_getconn(*args, **kwargs)
    monkeypatch.setattr(pool, "getconn", _wrapped)
    return calls


def test_connect_without_a_batch_opens_one_connection_per_call(tenant, monkeypatch):
    calls = _count_real_checkouts(monkeypatch)

    with storage.connect():
        pass
    with storage.connect():
        pass

    assert len(calls) == 2  # no active batch - each connect() call checks out its own, as before


def test_batch_session_reuses_one_connection_across_many_connect_calls(tenant, monkeypatch):
    # Confirmed real cost via cProfile (2026-08-16): a single plan_asset_
    # optimized run made ~14,000 separate connect() calls - this is the fix,
    # verified at the unit level: every connect() call inside one
    # batch_session must reuse the same underlying pooled connection.
    calls = _count_real_checkouts(monkeypatch)

    with storage.batch_session():
        for _ in range(20):
            with storage.connect():
                pass

    assert len(calls) == 1  # one real checkout for the whole batch, not 20


def test_connect_reverts_to_per_call_after_batch_session_exits(tenant, monkeypatch):
    calls = _count_real_checkouts(monkeypatch)

    with storage.batch_session():
        with storage.connect():
            pass
    with storage.connect():
        pass

    assert len(calls) == 2  # 1 for the batch + 1 for the call after it closed


def test_batch_session_writes_are_visible_after_it_commits(tenant):
    with storage.batch_session():
        storage.upsert_shortlist([])  # no-op write, just exercises a real storage function
        with storage.connect() as conn:
            conn.execute(
                "INSERT INTO shortlist (item_id, item, category, volume_m3, active) VALUES (999, 'x', 'y', 1.0, 1)"
            )

    with storage.connect() as conn:
        row = conn.execute("SELECT item FROM shortlist WHERE item_id = 999").fetchone()
    assert row == ("x",)


def test_batch_session_rolls_back_nothing_committed_on_exception(tenant):
    with pytest.raises(ValueError):
        with storage.batch_session():
            with storage.connect() as conn:
                conn.execute(
                    "INSERT INTO shortlist (item_id, item, category, volume_m3, active) VALUES (998, 'x', 'y', 1.0, 1)"
                )
            raise ValueError("boom")

    with storage.connect() as conn:
        row = conn.execute("SELECT item FROM shortlist WHERE item_id = 998").fetchone()
    assert row is None  # never committed - the batch raised before its own commit ran


def test_batch_session_is_reentrant(tenant, monkeypatch):
    calls = _count_real_checkouts(monkeypatch)

    with storage.batch_session():
        with storage.batch_session():  # nested call - must reuse, not check out a 2nd
            with storage.connect():
                pass

    assert len(calls) == 1


def test_with_batch_session_decorator_wraps_the_whole_call(tenant, monkeypatch):
    calls = _count_real_checkouts(monkeypatch)

    @storage.with_batch_session()
    def do_several_reads():
        for _ in range(5):
            with storage.connect():
                pass

    do_several_reads()

    assert len(calls) == 1

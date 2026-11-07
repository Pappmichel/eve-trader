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


def test_save_tenant_token_inside_a_batch_is_visible_to_other_connections_at_once(tenant):
    # A token refreshed mid-sync must be readable by every other connection
    # right away, not only once the surrounding owner batch commits.
    with storage.batch_session():
        storage.save_tenant_token("producer:1", {"access_token": "new"})
        with storage.connect_own_transaction() as other:
            row = other.execute("SELECT record FROM tenant_tokens WHERE role = 'producer:1'").fetchone()
        assert row == ({"access_token": "new"},)


def test_save_tenant_token_survives_a_batch_rollback(tenant):
    # EVE rotates refresh tokens - rolling a saved refresh back would lose
    # the only valid one.
    with pytest.raises(ValueError):
        with storage.batch_session():
            storage.save_tenant_token("producer:2", {"access_token": "kept"})
            raise ValueError("owner sync failed")

    assert storage.load_all_tenant_tokens()["producer:2"] == {"access_token": "kept"}


def test_savepoint_rolls_back_only_its_own_work_inside_a_batch(tenant):
    # A caught SQL error inside a batch used to abort the whole connection;
    # savepoint keeps later writes on the same batch viable.
    with storage.batch_session():
        with storage.connect() as conn:
            conn.execute(
                "INSERT INTO shortlist (item_id, item, category, volume_m3, active) "
                "VALUES (991, 'kept', 'y', 1.0, 1)"
            )
        with pytest.raises(Exception):
            with storage.savepoint():
                with storage.connect() as conn:
                    conn.execute("SELECT 1 FROM definitely_not_a_table")
        with storage.connect() as conn:
            conn.execute(
                "INSERT INTO shortlist (item_id, item, category, volume_m3, active) "
                "VALUES (992, 'after', 'y', 1.0, 1)"
            )

    with storage.connect() as conn:
        rows = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT item_id, item FROM shortlist WHERE item_id IN (991, 992)"
            ).fetchall()
        }
    assert rows == {991: "kept", 992: "after"}


def test_savepoint_is_a_noop_outside_a_batch(tenant, monkeypatch):
    calls = _count_real_checkouts(monkeypatch)
    with storage.savepoint():
        pass
    assert len(calls) == 0


def test_save_tenant_token_from_another_thread_does_not_block_on_an_open_batch(tenant):
    # The 2026-10-03 production deadlock: thread A saved a refreshed token
    # inside its open batch, thread B saved the same role and waited on A's
    # row lock forever. B must now finish while A's batch is still open.
    import threading

    done = threading.Event()

    def _other_thread():
        with storage.tenant_context(tenant):
            storage.save_tenant_token("producer:3", {"access_token": "b"})
        done.set()

    with storage.batch_session():
        storage.save_tenant_token("producer:3", {"access_token": "a"})
        worker = threading.Thread(target=_other_thread, daemon=True)
        worker.start()
        assert done.wait(timeout=10), "second save blocked on the open batch"

    assert storage.load_all_tenant_tokens()["producer:3"] == {"access_token": "b"}


def test_tenant_switch_inside_batch_reads_and_writes_under_inner_tenant(tenant, monkeypatch):
    """A tenant_context switch inside an open batch must not reuse the outer
    connection: app.tenant_id was fixed at batch open. Reads see only the
    inner tenant; a write under B survives A's rollback."""
    tenant_a = storage.create_tenant("batch-switch-a")
    tenant_b = storage.create_tenant("batch-switch-b")

    calls = _count_real_checkouts(monkeypatch)
    with storage.tenant_context(tenant_a):
        with pytest.raises(ValueError, match="roll back A"):
            with storage.batch_session():
                with storage.connect() as conn:
                    conn.execute(
                        "INSERT INTO shortlist (item_id, item, category, volume_m3, active) "
                        "VALUES (801, 'a-only', 'y', 1.0, 1)"
                    )
                with storage.tenant_context(tenant_b):
                    # Must open a fresh connection for B (not the A batch).
                    before = len(calls)
                    with storage.connect() as conn:
                        conn.execute(
                            "INSERT INTO shortlist (item_id, item, category, volume_m3, active) "
                            "VALUES (802, 'b-only', 'y', 1.0, 1)"
                        )
                    assert len(calls) > before
                    with storage.connect() as conn:
                        rows = conn.execute(
                            "SELECT item FROM shortlist WHERE item_id IN (801, 802) "
                            "ORDER BY item_id"
                        ).fetchall()
                    assert rows == [("b-only",)]
                # Back under A's batch: still sees only A's uncommitted write.
                with storage.connect() as conn:
                    rows = conn.execute(
                        "SELECT item FROM shortlist WHERE item_id IN (801, 802) "
                        "ORDER BY item_id"
                    ).fetchall()
                assert rows == [("a-only",)]
                raise ValueError("roll back A")

    # A's batch rolled back; B's own-transaction write committed.
    with storage.tenant_context(tenant_a):
        with storage.connect() as conn:
            assert conn.execute(
                "SELECT item FROM shortlist WHERE item_id = 801"
            ).fetchone() is None
    with storage.tenant_context(tenant_b):
        with storage.connect() as conn:
            assert conn.execute(
                "SELECT item FROM shortlist WHERE item_id = 802"
            ).fetchone() == ("b-only",)


def test_nested_batch_under_other_tenant_restores_outer(tenant, monkeypatch):
    tenant_a = storage.create_tenant("nested-batch-a")
    tenant_b = storage.create_tenant("nested-batch-b")

    calls = _count_real_checkouts(monkeypatch)
    with storage.tenant_context(tenant_a):
        with storage.batch_session():
            outer_conn = storage._batch_local.conn
            with storage.connect() as conn:
                conn.execute(
                    "INSERT INTO shortlist (item_id, item, category, volume_m3, active) "
                    "VALUES (811, 'outer', 'y', 1.0, 1)"
                )
            with storage.tenant_context(tenant_b):
                with storage.batch_session():
                    assert storage._batch_local.conn is not outer_conn
                    assert storage._batch_local.tenant_id == tenant_b
                    with storage.connect() as conn:
                        conn.execute(
                            "INSERT INTO shortlist (item_id, item, category, volume_m3, active) "
                            "VALUES (812, 'inner', 'y', 1.0, 1)"
                        )
            # Outer batch restored.
            assert storage._batch_local.conn is outer_conn
            assert storage._batch_local.tenant_id == tenant_a
            with storage.connect() as conn:
                rows = conn.execute(
                    "SELECT item FROM shortlist WHERE item_id IN (811, 812) ORDER BY item_id"
                ).fetchall()
            assert rows == [("outer",)]

    assert len(calls) == 2  # one for A batch, one for nested B batch
    with storage.tenant_context(tenant_b):
        with storage.connect() as conn:
            assert conn.execute(
                "SELECT item FROM shortlist WHERE item_id = 812"
            ).fetchone() == ("inner",)

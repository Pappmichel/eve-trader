import sqlite3

import pytest

from eve_trader import storage


def _count_real_connects(monkeypatch):
    """Wraps sqlite3.connect so real connection counts can be asserted on,
    without changing its actual behavior."""
    calls = []
    real_connect = sqlite3.connect

    def _wrapped(*args, **kwargs):
        calls.append(1)
        return real_connect(*args, **kwargs)
    monkeypatch.setattr(storage.sqlite3, "connect", _wrapped)
    return calls


def test_connect_without_a_batch_opens_one_connection_per_call(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    calls = _count_real_connects(monkeypatch)

    with storage.connect(db_path):
        pass
    with storage.connect(db_path):
        pass

    assert len(calls) == 2  # no active batch - each connect() call opens its own, as before


def test_batch_session_reuses_one_connection_across_many_connect_calls(tmp_path, monkeypatch):
    # Confirmed real cost via cProfile (2026-08-16): a single plan_asset_
    # optimized run made ~14,000 separate connect()/close() cycles - this is
    # the fix, verified at the unit level: every connect() call inside one
    # batch_session must reuse the same underlying sqlite3 connection.
    db_path = tmp_path / "test.db"
    calls = _count_real_connects(monkeypatch)

    with storage.batch_session(db_path):
        for _ in range(20):
            with storage.connect(db_path):
                pass

    assert len(calls) == 1  # one real connection for the whole batch, not 20


def test_connect_reverts_to_per_call_after_batch_session_exits(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    calls = _count_real_connects(monkeypatch)

    with storage.batch_session(db_path):
        with storage.connect(db_path):
            pass
    with storage.connect(db_path):
        pass

    assert len(calls) == 2  # 1 for the batch + 1 for the call after it closed


def test_batch_session_writes_are_visible_after_it_commits(tmp_path):
    db_path = tmp_path / "test.db"
    with storage.batch_session(db_path):
        storage.upsert_shortlist([], db_path=db_path)  # no-op write, just exercises a real storage function
        with storage.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO shortlist (item_id, item, category, volume_m3, active) VALUES (999, 'x', 'y', 1.0, 1)"
            )

    with storage.connect(db_path) as conn:
        row = conn.execute("SELECT item FROM shortlist WHERE item_id = 999").fetchone()
    assert row == ("x",)


def test_batch_session_rolls_back_nothing_committed_on_exception(tmp_path):
    db_path = tmp_path / "test.db"
    with pytest.raises(ValueError):
        with storage.batch_session(db_path):
            with storage.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO shortlist (item_id, item, category, volume_m3, active) VALUES (998, 'x', 'y', 1.0, 1)"
                )
            raise ValueError("boom")

    with storage.connect(db_path) as conn:
        row = conn.execute("SELECT item FROM shortlist WHERE item_id = 998").fetchone()
    assert row is None  # never committed - the batch raised before its own commit ran


def test_batch_session_is_reentrant_for_the_same_db_path(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    calls = _count_real_connects(monkeypatch)

    with storage.batch_session(db_path):
        with storage.batch_session(db_path):  # nested call for the same db_path - must reuse, not open a 2nd
            with storage.connect(db_path):
                pass

    assert len(calls) == 1


def test_batch_session_raises_when_nested_for_a_different_db_path(tmp_path):
    db_path_a = tmp_path / "a.db"
    db_path_b = tmp_path / "b.db"

    with storage.batch_session(db_path_a):
        with pytest.raises(RuntimeError):
            with storage.batch_session(db_path_b):
                pass


def test_with_batch_session_decorator_wraps_the_whole_call(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    calls = _count_real_connects(monkeypatch)

    @storage.with_batch_session(db_path)
    def do_several_reads():
        for _ in range(5):
            with storage.connect(db_path):
                pass

    do_several_reads()

    assert len(calls) == 1

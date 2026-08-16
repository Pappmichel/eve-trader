import sqlite3
import zipfile

import pytest

from eve_trader import backup


@pytest.fixture
def isolated_backup_dir(tmp_path, monkeypatch):
    # Real filesystem (zipfile/sqlite3 need real paths), but under pytest's
    # tmp_path - never touches the real data/backups/ or data/eve_trader.db.
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", backup_dir)

    db_path = tmp_path / "eve_trader.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(backup.storage, "DB_PATH", db_path)

    config_path = tmp_path / "config.yaml"
    config_path.write_text("jita_region_id: 10000002\n")
    monkeypatch.setattr(backup, "DEFAULT_CONFIG_PATH", config_path)

    class _FakeOAuthConfig:
        token_store_path = tmp_path / "tokens.json"  # deliberately absent - optional file

    monkeypatch.setattr(backup, "OAUTH_CONFIG", _FakeOAuthConfig())

    return backup_dir, db_path, config_path


def test_create_backup_produces_a_zip_with_db_and_config(isolated_backup_dir):
    backup_dir, db_path, config_path = isolated_backup_dir

    info = backup.create_backup()

    zip_path = backup_dir / info["name"]
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "eve_trader.db" in names
        assert "config.yaml" in names
        assert "tokens.json" not in names  # wasn't present on disk - correctly skipped, not a hard error

        # Restored DB content is real, not an empty/corrupt placeholder -
        # proves the sqlite backup API path actually worked, not just zipped nothing.
        zf.extract("eve_trader.db", backup_dir)
    conn = sqlite3.connect(backup_dir / "eve_trader.db")
    assert conn.execute("SELECT x FROM t").fetchone() == (42,)
    conn.close()


def test_create_backup_includes_tokens_when_present(isolated_backup_dir):
    backup_dir, db_path, config_path = isolated_backup_dir
    backup.OAUTH_CONFIG.token_store_path.write_text('{"buyer": "fake"}')

    info = backup.create_backup()

    with zipfile.ZipFile(backup_dir / info["name"]) as zf:
        assert "tokens.json" in zf.namelist()


def test_list_backups_empty_when_none_exist(isolated_backup_dir):
    assert backup.list_backups() == []


def _fake_clock(monkeypatch, start_hour=0):
    # Backup filenames are second-resolution timestamps ("%Y-%m-%dT%H-%M-%SZ")
    # - two real create_backup() calls within the same wall-clock second
    # would collide on the same filename. A monotonically-advancing fake
    # clock (1 hour/call) proves newest-first ordering and pruning without
    # a real sleep() per call.
    import datetime as real_dt
    state = {"hour": start_hour}

    class _FakeDateTime(real_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            state["hour"] += 1
            return real_dt.datetime(2026, 1, 1, tzinfo=real_dt.timezone.utc) + real_dt.timedelta(hours=state["hour"])

    monkeypatch.setattr(backup, "datetime", _FakeDateTime)


def test_list_backups_returns_newest_first(isolated_backup_dir, monkeypatch):
    _fake_clock(monkeypatch)

    backup.create_backup()
    second = backup.create_backup()

    rows = backup.list_backups()

    assert len(rows) == 2
    assert rows[0]["name"] == second["name"]


def test_prune_keeps_only_max_backups(isolated_backup_dir, monkeypatch):
    monkeypatch.setattr(backup, "MAX_BACKUPS", 2)
    _fake_clock(monkeypatch)

    for _ in range(4):
        backup.create_backup()

    assert len(backup.list_backups()) == 2

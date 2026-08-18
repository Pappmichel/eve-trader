import zipfile

import pytest

from eve_trader import backup


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr


def _fake_pg_dump_run(dump_bytes=b"FAKE_PG_DUMP_CONTENT", returncode=0, fake_stderr=b""):
    """Stands in for subprocess.run(["docker", "exec", ..., "pg_dump", ...],
    stdout=<file>, ...) - a real test run can't depend on Docker/Postgres
    being reachable, so this just writes known bytes to the same file
    handle create_backup() passes as `stdout=`, mirroring what a real
    subprocess would have written there."""
    def _run(cmd, stdout=None, stderr=None, timeout=None):
        if stdout is not None:
            stdout.write(dump_bytes)
        return _FakeCompletedProcess(returncode=returncode, stderr=fake_stderr)
    return _run


@pytest.fixture
def isolated_backup_dir(tmp_path, monkeypatch):
    # Real filesystem (zipfile needs real paths), but under pytest's
    # tmp_path - never touches the real data/backups/.
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", backup_dir)

    config_path = tmp_path / "config.yaml"
    config_path.write_text("jita_region_id: 10000002\n")
    monkeypatch.setattr(backup, "DEFAULT_CONFIG_PATH", config_path)

    monkeypatch.setattr(backup.subprocess, "run", _fake_pg_dump_run())

    return backup_dir, config_path


def test_create_backup_produces_a_zip_with_dump_and_config(isolated_backup_dir):
    backup_dir, config_path = isolated_backup_dir

    info = backup.create_backup()

    zip_path = backup_dir / info["name"]
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "eve_trader.dump" in names
        assert "config.yaml" in names
        assert "tokens.json" not in names  # never included anymore - TokenManager persists to Postgres now

        # Real bytes made it through the subprocess -> file -> zip path, not
        # an empty/corrupt placeholder.
        assert zf.read("eve_trader.dump") == b"FAKE_PG_DUMP_CONTENT"


def test_create_backup_raises_and_cleans_up_on_pg_dump_failure(isolated_backup_dir, monkeypatch):
    backup_dir, config_path = isolated_backup_dir
    monkeypatch.setattr(backup.subprocess, "run",
                         _fake_pg_dump_run(returncode=1, fake_stderr=b"connection refused"))

    with pytest.raises(RuntimeError, match="connection refused"):
        backup.create_backup()

    # No partial/corrupt zip or orphaned .tmp_*.dump left behind.
    assert list(backup_dir.glob(f"{backup.BACKUP_NAME_PREFIX}*.zip")) == []
    assert list(backup_dir.glob(".tmp_*.dump")) == []


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

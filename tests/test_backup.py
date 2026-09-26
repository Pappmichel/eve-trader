import os
import stat
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


def test_create_backup_is_never_group_or_world_readable_regardless_of_umask(isolated_backup_dir):
    """T1-05 regression (found in Plan 2's independent challenge pass,
    2026-09-26): a real backup created over a non-interactive SSH session
    inherited the OS default umask (0002), not the interactive-shell-only
    `umask 077` this repo relies on elsewhere - confirmed live, it produced
    a world-readable pg_dump of the whole database (tenant_tokens
    included). create_backup() must chmod its own output explicitly,
    never depend on whatever umask the calling process happens to have."""
    backup_dir, _config_path = isolated_backup_dir
    old_umask = os.umask(0o002)  # the exact permissive umask this bug was found under
    try:
        info = backup.create_backup()
    finally:
        os.umask(old_umask)

    zip_path = backup_dir / info["name"]
    mode = stat.S_IMODE(zip_path.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_create_backup_raises_and_cleans_up_on_pg_dump_failure(isolated_backup_dir, monkeypatch):
    backup_dir, config_path = isolated_backup_dir
    monkeypatch.setattr(backup.subprocess, "run",
                         _fake_pg_dump_run(returncode=1, fake_stderr=b"connection refused password=secret"))

    with pytest.raises(backup.BackupError, match=r"^Database backup failed\.$") as ei:
        backup.create_backup()
    assert "connection refused" not in str(ei.value)
    assert "password" not in str(ei.value)
    assert "secret" not in str(ei.value)

    # No partial/corrupt zip or orphaned .tmp_*.dump left behind.
    assert list(backup_dir.glob(f"{backup.BACKUP_NAME_PREFIX}*.zip")) == []
    assert list(backup_dir.glob(".tmp_*.dump")) == []


def test_create_backup_wraps_pg_dump_in_docker_exec_by_default(isolated_backup_dir, monkeypatch):
    calls = []

    def _run(cmd, stdout=None, stderr=None, timeout=None, shell=False):
        calls.append({"cmd": cmd, "shell": shell})
        if stdout is not None:
            stdout.write(b"x")
        return _FakeCompletedProcess()
    monkeypatch.setattr(backup.subprocess, "run", _run)

    backup.create_backup()

    assert calls[0]["shell"] is False
    assert isinstance(calls[0]["cmd"], list)
    assert calls[0]["cmd"][:2] == [backup.DOCKER_BIN, "exec"]
    assert "pg_dump" in calls[0]["cmd"]


def test_create_backup_uses_bare_pg_dump_when_container_is_empty(isolated_backup_dir, monkeypatch):
    # EVE_TRADER_PG_CONTAINER="" - a host running Postgres natively (no
    # Docker at all, e.g. the live deploy's ~1GB-RAM VM - see
    # deploy/README.md's Postgres section) needs no docker exec wrapper.
    monkeypatch.setattr(backup, "PG_CONTAINER", "")
    calls = []

    def _run(cmd, stdout=None, stderr=None, timeout=None, shell=False):
        calls.append(cmd)
        assert shell is False
        if stdout is not None:
            stdout.write(b"x")
        return _FakeCompletedProcess()
    monkeypatch.setattr(backup.subprocess, "run", _run)

    backup.create_backup()

    assert calls[0][0] == "pg_dump"
    assert "docker" not in calls[0]
    assert "exec" not in calls[0]


def test_list_backups_empty_when_none_exist(isolated_backup_dir):
    assert backup.list_backups() == []


def _fake_clock(monkeypatch, start_hour=0):
    # Names also include microseconds + a uuid, so same-second calls no
    # longer collide (F-NEW-03). A monotonically-advancing fake clock still
    # makes newest-first ordering and pruning deterministic.
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


def test_backup_names_are_unique_even_with_a_frozen_clock(isolated_backup_dir, monkeypatch):
    import datetime as real_dt

    class _FrozenDateTime(real_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return real_dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=real_dt.timezone.utc)

    monkeypatch.setattr(backup, "datetime", _FrozenDateTime)
    first = backup.create_backup()
    second = backup.create_backup()
    assert first["name"] != second["name"]
    assert first["name"].startswith(backup.BACKUP_NAME_PREFIX)
    assert second["name"].startswith(backup.BACKUP_NAME_PREFIX)


def test_parallel_create_backup_does_not_collide_or_corrupt(isolated_backup_dir):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _i: backup.create_backup(), range(8)))

    names = [r["name"] for r in results]
    assert len(names) == 8
    assert len(set(names)) == 8
    listed = {row["name"] for row in backup.list_backups()}
    assert listed == set(names)

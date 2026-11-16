import os
import stat
import sys
import zipfile

import pytest

from eve_trader import backup

# Windows has no group/other permission bits: chmod(0o600) only toggles the
# read-only flag, so a file always reports 0o666. Production runs on Linux.
posix_modes_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


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


@posix_modes_only
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


@posix_modes_only
def test_create_backup_chmods_the_tmp_dump_before_pg_dump_writes_to_it(isolated_backup_dir, monkeypatch):
    """Second independent challenge pass (2026-09-26): the first version of
    the T1-05 fix only chmod'd the tmp dump file *after* subprocess.run()
    returned - under a permissive umask, that narrowed the exposure window
    from "forever" to "the entire pg_dump duration" (up to the 300s
    timeout) instead of actually closing it. The file must already be 0600
    by the time pg_dump (or its stand-in here) starts writing to it, not
    only once it's done."""
    backup_dir, _config_path = isolated_backup_dir
    seen_modes = []

    def _run(cmd, stdout=None, stderr=None, timeout=None):
        seen_modes.append(stat.S_IMODE(os.fstat(stdout.fileno()).st_mode))
        stdout.write(b"FAKE_PG_DUMP_CONTENT")
        return _FakeCompletedProcess(returncode=0, stderr=b"")

    monkeypatch.setattr(backup.subprocess, "run", _run)

    old_umask = os.umask(0o002)  # the exact permissive umask T1-05 was found under
    try:
        backup.create_backup()
    finally:
        os.umask(old_umask)

    assert seen_modes == [0o600]


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


def test_predeploy_prefix_has_its_own_name_family_and_retention(isolated_backup_dir, monkeypatch):
    backup_dir, _ = isolated_backup_dir
    monkeypatch.setattr(backup, "MAX_PREDEPLOY_BACKUPS", 2)
    _fake_clock(monkeypatch)

    daily = [backup.create_backup() for _ in range(3)]
    pre = [backup.create_backup(prefix=backup.PREDEPLOY_BACKUP_PREFIX) for _ in range(4)]

    assert all(i["name"].startswith("eve_trader_predeploy_") for i in pre)
    pre_files = sorted(p.name for p in backup_dir.glob("eve_trader_predeploy_*.zip"))
    assert pre_files == sorted(i["name"] for i in pre[-2:])
    # Daily backups are untouched by pre-deploy rotation, and the Admin list
    # does not show pre-deploy files.
    assert {r["name"] for r in backup.list_backups()} == {i["name"] for i in daily}


def test_predeploy_backup_is_not_counted_against_daily_retention(isolated_backup_dir, monkeypatch):
    monkeypatch.setattr(backup, "MAX_BACKUPS", 2)
    _fake_clock(monkeypatch)
    backup.create_backup()
    backup.create_backup()
    for _ in range(3):
        backup.create_backup(prefix=backup.PREDEPLOY_BACKUP_PREFIX)
    assert len(backup.list_backups()) == 2


def test_create_backup_rejects_an_unknown_prefix(isolated_backup_dir):
    with pytest.raises(ValueError):
        backup.create_backup(prefix="../evil_")


@posix_modes_only
def test_predeploy_backup_is_owner_only(isolated_backup_dir):
    backup_dir, _ = isolated_backup_dir
    info = backup.create_backup(prefix=backup.PREDEPLOY_BACKUP_PREFIX)
    assert stat.S_IMODE((backup_dir / info["name"]).stat().st_mode) == 0o600


def test_cli_backup_create_calls_create_backup(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from eve_trader import cli

    calls = []
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(
        backup, "create_backup",
        lambda prefix=backup.BACKUP_NAME_PREFIX: calls.append(prefix) or {"name": "x.zip", "size_bytes": 3},
    )
    # The group callback resolves tenant config from the database; the backup
    # command itself does not need it.
    monkeypatch.setattr(cli.config, "resolve_and_set_trading_config", lambda *_: None)
    monkeypatch.setattr(cli.production_config, "resolve_and_set_production_config", lambda *_: None)

    res = CliRunner().invoke(cli.main, ["backup", "create", "--pre-deploy"])
    assert res.exit_code == 0, res.output
    assert calls == [backup.PREDEPLOY_BACKUP_PREFIX]
    assert "x.zip" in res.output


@pytest.fixture(autouse=True)
def _restore_ambient_tenant():
    """cli.main sets the default tenant for the process; put it back so later
    tests in the same worker see no ambient tenant."""
    from eve_trader import storage

    token = storage.set_current_tenant(None)
    yield
    storage.reset_current_tenant(token)


def test_cli_backup_create_exits_1_on_failure(monkeypatch):
    from click.testing import CliRunner
    from eve_trader import cli

    def boom(prefix=backup.BACKUP_NAME_PREFIX):
        raise backup.BackupError("Database backup failed.")

    monkeypatch.setattr(backup, "create_backup", boom)
    monkeypatch.setattr(cli.config, "resolve_and_set_trading_config", lambda *_: None)
    monkeypatch.setattr(cli.production_config, "resolve_and_set_production_config", lambda *_: None)
    res = CliRunner().invoke(cli.main, ["backup", "create"])
    assert res.exit_code == 1


def _capture_cmd(monkeypatch):
    calls = []

    def _run(cmd, stdout=None, stderr=None, timeout=None, shell=False):
        calls.append(cmd)
        if stdout is not None:
            stdout.write(b"x")
        return _FakeCompletedProcess()
    monkeypatch.setattr(backup.subprocess, "run", _run)
    return calls


def test_bare_pg_dump_defaults_to_the_postgres_role(isolated_backup_dir, monkeypatch):
    monkeypatch.setattr(backup, "PG_CONTAINER", "")
    monkeypatch.delenv("EVE_TRADER_PG_BACKUP_USER", raising=False)
    calls = _capture_cmd(monkeypatch)
    backup.create_backup()
    cmd = calls[0]
    assert cmd[cmd.index("-U") + 1] == "postgres"


def test_bare_pg_dump_uses_the_configured_backup_role(isolated_backup_dir, monkeypatch):
    monkeypatch.setattr(backup, "PG_CONTAINER", "")
    monkeypatch.setenv("EVE_TRADER_PG_BACKUP_USER", "eve_trader_backup")
    calls = _capture_cmd(monkeypatch)
    backup.create_backup()
    cmd = calls[0]
    assert cmd[cmd.index("-U") + 1] == "eve_trader_backup"


def test_docker_pg_dump_ignores_the_backup_role_setting(isolated_backup_dir, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_PG_BACKUP_USER", "eve_trader_backup")
    calls = _capture_cmd(monkeypatch)
    backup.create_backup()
    cmd = calls[0]
    assert cmd[cmd.index("-U") + 1] == "postgres"

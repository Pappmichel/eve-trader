import pytest

from eve_trader import actions, backup


def test_do_create_backup_returns_result_on_success(monkeypatch):
    monkeypatch.setattr(backup, "create_backup", lambda: {"name": "x.zip", "created_at": "t", "size_bytes": 1})

    result = actions.do_create_backup()

    assert result == {"name": "x.zip", "created_at": "t", "size_bytes": 1}


def test_do_create_backup_converts_pg_dump_failure_to_action_error(monkeypatch):
    # backup.create_backup() raises BackupError (a RuntimeError) on a
    # non-zero pg_dump exit. The ActionError message must stay generic so
    # an HTTP 400 cannot leak stderr (F-NEW-04).
    def boom():
        raise RuntimeError("pg_dump failed (exit 1): connection refused")
    monkeypatch.setattr(backup, "create_backup", boom)

    with pytest.raises(actions.ActionError, match=r"^Backup failed\.$") as ei:
        actions.do_create_backup()
    assert "connection refused" not in str(ei.value)


def test_do_create_backup_converts_os_error_to_action_error(monkeypatch):
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(backup, "create_backup", boom)

    with pytest.raises(actions.ActionError, match=r"^Backup failed\.$") as ei:
        actions.do_create_backup()
    assert "disk full" not in str(ei.value)

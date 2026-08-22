from eve_trader import error_log, storage


class _FakeConn:
    """Records every SQL statement executed against it - used to verify
    storage.log_error's own prune-after-insert query without touching a
    real (and, for this table, shared/persistent across worktrees) Postgres
    instance."""

    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))


def test_log_error_prunes_after_inserting(monkeypatch):
    fake_conn = _FakeConn()
    monkeypatch.setattr(storage, "connect_unscoped", lambda: fake_conn)
    monkeypatch.setattr(storage, "get_current_tenant", lambda: None)

    storage.log_error("frontend", "boom", None, None)

    assert len(fake_conn.statements) == 2
    insert_sql, _ = fake_conn.statements[0]
    delete_sql, delete_params = fake_conn.statements[1]
    assert "INSERT INTO error_log" in insert_sql
    assert "DELETE FROM error_log" in delete_sql
    assert delete_params == (storage.MAX_ERROR_LOG_ROWS,)


def test_do_report_error_passes_through_to_storage(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(storage, "log_error", _capture)

    result = error_log.do_report_error(source="frontend", message="boom", detail="stack trace", path="/trading")

    assert result == {"recorded": True}
    assert captured == {"source": "frontend", "message": "boom", "detail": "stack trace", "path": "/trading"}


def test_do_report_error_treats_missing_detail_and_path_as_none(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(storage, "log_error", _capture)

    error_log.do_report_error(source="frontend", message="boom")

    assert captured["detail"] is None
    assert captured["path"] is None


def test_do_report_error_truncates_an_oversized_message(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(storage, "log_error", _capture)

    error_log.do_report_error(source="frontend", message="x" * 10_000, detail="y" * 10_000, path="z" * 10_000)

    assert len(captured["message"]) == error_log._MAX_MESSAGE_LENGTH
    assert len(captured["detail"]) == error_log._MAX_DETAIL_LENGTH
    assert len(captured["path"]) == error_log._MAX_PATH_LENGTH


def test_do_report_error_degrades_to_recorded_false_on_storage_failure(monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(storage, "log_error", _raise)

    result = error_log.do_report_error(source="frontend", message="boom")

    assert result == {"recorded": False}


def test_do_list_errors_passes_through_to_storage(monkeypatch):
    monkeypatch.setattr(storage, "list_errors", lambda limit: [{"id": 1, "message": "boom"}] if limit == 50 else [])

    assert error_log.do_list_errors(limit=50) == [{"id": 1, "message": "boom"}]

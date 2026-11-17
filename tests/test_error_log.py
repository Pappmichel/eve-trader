from eve_trader import error_log, storage


class _FakeConn:
    """Records every SQL statement executed against it - used to verify
    storage.log_error's own retention queries without touching a real
    Postgres instance. `counts` answers the COUNT queries in order."""

    def __init__(self, counts=(1, 1)):
        self.statements = []
        self._counts = list(counts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def fetchone(self):
        return (self._counts.pop(0),)


def test_log_error_does_not_delete_while_under_the_caps(monkeypatch):
    fake_conn = _FakeConn(counts=(10, 10))
    monkeypatch.setattr(storage, "connect_unscoped", lambda: fake_conn)
    monkeypatch.setattr(storage, "get_current_tenant", lambda: None)

    storage.log_error("frontend", "boom", None, None)

    sqls = [sql for sql, _ in fake_conn.statements]
    assert "INSERT INTO error_log" in sqls[0]
    assert not any(sql.startswith("DELETE") for sql in sqls)


def test_log_error_prunes_the_tenant_once_over_cap_plus_slack(monkeypatch):
    over = storage.MAX_ERROR_LOG_ROWS_PER_TENANT + storage.ERROR_LOG_PRUNE_SLACK + 1
    fake_conn = _FakeConn(counts=(over, over))
    monkeypatch.setattr(storage, "connect_unscoped", lambda: fake_conn)
    monkeypatch.setattr(storage, "get_current_tenant", lambda: None)

    storage.log_error("frontend", "boom", None, None)

    deletes = [(sql, p) for sql, p in fake_conn.statements if sql.startswith("DELETE")]
    assert len(deletes) == 1
    assert "IS NOT DISTINCT FROM" in deletes[0][0]
    assert deletes[0][1][-1] == storage.MAX_ERROR_LOG_ROWS_PER_TENANT


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


def test_group_errors_merges_ids_and_numbers():
    rows = [
        {"id": 3, "tenant_id": "t2", "source": "frontend", "message": "fail 3 11111111-2222-3333-4444-555555555555",
         "detail": "newest", "path": "/b", "created_at": "2026-11-03T00:00:00"},
        {"id": 2, "tenant_id": "t1", "source": "frontend", "message": "fail 2 aaaaaaaa-2222-3333-4444-555555555555",
         "detail": "older", "path": "/a", "created_at": "2026-11-02T00:00:00"},
        {"id": 1, "tenant_id": "t1", "source": "backend", "message": "fail 1 aaaaaaaa-2222-3333-4444-555555555555",
         "detail": None, "path": None, "created_at": "2026-11-01T00:00:00"},
    ]
    groups = error_log.group_errors(rows)
    assert len(groups) == 2
    g = groups[0]
    assert (g["source"], g["count"], g["tenant_count"]) == ("frontend", 2, 2)
    assert g["first_seen"] == "2026-11-02T00:00:00" and g["last_seen"] == "2026-11-03T00:00:00"
    assert (g["detail"], g["path"]) == ("newest", "/b")

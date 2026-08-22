from eve_trader import error_log, storage


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


def test_do_list_errors_passes_through_to_storage(monkeypatch):
    monkeypatch.setattr(storage, "list_errors", lambda limit: [{"id": 1, "message": "boom"}] if limit == 50 else [])

    assert error_log.do_list_errors(limit=50) == [{"id": 1, "message": "boom"}]

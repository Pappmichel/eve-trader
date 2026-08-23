"""Router-level tests for /api/errors (report endpoint) - see
test_api_routers.py's own docstring for the pattern this follows (every
error_log.do_* call monkeypatched, never touches real Postgres).
error_log.py's own do_* logic has its own coverage in test_error_log.py.
"""
import pytest
from fastapi.testclient import TestClient

from eve_trader import error_log
from eve_trader.api.app import create_app
from eve_trader.api.routers import errors as errors_router

client = TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    # Module-level cache (see CLAUDE.md's "Testing conventions" - any
    # function with a module-level cache needs this, or an earlier test's
    # hit count leaks into a later one).
    errors_router._rate_limit_hits.clear()
    yield
    errors_router._rate_limit_hits.clear()


def test_report_error_passes_body_fields_to_action(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"recorded": True}
    monkeypatch.setattr(error_log, "do_report_error", _capture)

    resp = client.post("/api/errors", json={
        "source": "frontend", "message": "TypeError: boom", "detail": "at foo.tsx:12", "path": "/trading",
    })

    assert resp.status_code == 200
    assert resp.json() == {"recorded": True}
    assert captured == {
        "source": "frontend", "message": "TypeError: boom", "detail": "at foo.tsx:12", "path": "/trading",
    }


def test_report_error_allows_omitting_optional_fields(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"recorded": True}
    monkeypatch.setattr(error_log, "do_report_error", _capture)

    resp = client.post("/api/errors", json={"source": "frontend", "message": "boom"})

    assert resp.status_code == 200
    assert captured == {"source": "frontend", "message": "boom", "detail": None, "path": None}


def test_report_error_missing_required_field_is_a_422_not_a_500(monkeypatch):
    resp = client.post("/api/errors", json={"source": "frontend"})  # no message
    assert resp.status_code == 422


def test_report_error_oversized_message_is_a_422(monkeypatch):
    # No auth/tool-grant gate at all on this endpoint (see the router's own
    # docstring on why) - length caps are the one thing standing between an
    # anonymous caller and an unbounded write into error_log.
    resp = client.post("/api/errors", json={"source": "frontend", "message": "x" * 2001})
    assert resp.status_code == 422


def test_report_error_rate_limits_after_too_many_requests_from_one_source(monkeypatch):
    monkeypatch.setattr(error_log, "do_report_error", lambda **kwargs: {"recorded": True})

    for _ in range(errors_router._RATE_LIMIT_MAX_REQUESTS):
        resp = client.post("/api/errors", json={"source": "frontend", "message": "boom"})
        assert resp.status_code == 200

    resp = client.post("/api/errors", json={"source": "frontend", "message": "boom"})
    assert resp.status_code == 429

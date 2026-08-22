"""Router-level tests for /api/errors (report endpoint) - see
test_api_routers.py's own docstring for the pattern this follows (every
error_log.do_* call monkeypatched, never touches real Postgres).
error_log.py's own do_* logic has its own coverage in test_error_log.py.
"""
from fastapi.testclient import TestClient

from eve_trader import error_log
from eve_trader.api.app import create_app

client = TestClient(create_app())


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

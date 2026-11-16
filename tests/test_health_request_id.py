"""/api/health (public, status only), /api/admin/health (admin grant) and the
X-Request-ID middleware plus its logging filter. No real Postgres: the
database probe and the gate's session lookup are monkeypatched."""
from __future__ import annotations

import logging
from contextlib import contextmanager, nullcontext

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, admin, storage
from eve_trader.api import app as app_module
from eve_trader.api.app import create_app
from eve_trader.api.routers import health as health_router
from eve_trader.logging_setup import RequestIdFilter
from eve_trader.request_context import accept_or_generate, request_id_var

client = TestClient(create_app())


class _Conn:
    def execute(self, *_a, **_k):
        return self

    def fetchone(self):
        return (1,)


@contextmanager
def _ok_connect(timeout=None):
    yield _Conn()


def _session(tool_keys):
    return access_gate.AuthorizedSession(
        character_id=7, character_name="Pilot", tenant_id="t1", tool_keys=tool_keys,
    )


@pytest.mark.gate_enforced
def test_health_is_200_without_cookie_while_gate_is_on(monkeypatch):
    seen = {}

    @contextmanager
    def connect(timeout=None):
        seen["timeout"] = timeout
        yield _Conn()

    monkeypatch.setattr(storage, "connect_unscoped", connect)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert seen["timeout"] == health_router.DB_CHECK_TIMEOUT_SECONDS


def test_health_is_503_when_the_database_fails(monkeypatch):
    @contextmanager
    def broken(timeout=None):
        raise RuntimeError("password=secret host=db")
        yield  # pragma: no cover

    monkeypatch.setattr(storage, "connect_unscoped", broken)
    resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded"}
    assert "secret" not in resp.text


def test_health_is_503_when_the_pool_is_exhausted(monkeypatch):
    psycopg_pool = pytest.importorskip("psycopg_pool")

    @contextmanager
    def exhausted(timeout=None):
        raise psycopg_pool.PoolTimeout("couldn't get a connection after 2.00 sec")
        yield  # pragma: no cover

    monkeypatch.setattr(storage, "connect_unscoped", exhausted)
    assert client.get("/api/health").status_code == 503


@pytest.mark.gate_enforced
def test_admin_health_is_403_without_admin_grant(monkeypatch):
    monkeypatch.setattr(app_module, "authorize_session_cookie", lambda _c: _session(["portfolio"]))
    monkeypatch.setattr(app_module, "note_tenant_activity", lambda _t: None)
    monkeypatch.setattr(admin, "do_get_health_detail", lambda: pytest.fail("must not run"))
    resp = client.get("/api/admin/health", cookies={access_gate.SESSION_COOKIE_NAME: "x"})
    assert resp.status_code == 403


@pytest.mark.gate_enforced
def test_admin_health_is_401_without_cookie():
    assert client.get("/api/admin/health").status_code == 401


@pytest.mark.gate_enforced
def test_admin_health_returns_detail_with_admin_grant(monkeypatch):
    monkeypatch.setattr(app_module, "authorize_session_cookie", lambda _c: _session(["admin"]))
    monkeypatch.setattr(app_module, "note_tenant_activity", lambda _t: None)
    detail = {
        "version": "0.3.0", "newest_backup_at": None, "newest_backup_age_seconds": None,
        "newest_esi_sync_at": None, "scheduler_running": False,
    }
    monkeypatch.setattr(admin, "do_get_health_detail", lambda: detail)
    # The gate resolves the tenant's config from the database; not under test.
    monkeypatch.setattr(app_module.tenant_scope, "enter_tenant", lambda _t: nullcontext())
    resp = client.get("/api/admin/health", cookies={access_gate.SESSION_COOKIE_NAME: "x"})
    assert resp.status_code == 200
    assert resp.json() == detail


def test_health_detail_reads_version_backup_and_scheduler(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from eve_trader import backup, tenant_scope

    created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    monkeypatch.setattr(backup, "list_backups", lambda: [{"name": "b.zip", "created_at": created, "size_bytes": 1}])

    @contextmanager
    def fake_enter(tenant_id):
        assert tenant_id == storage.DEFAULT_TENANT_ID
        yield

    monkeypatch.setattr(tenant_scope, "enter_tenant", fake_enter)
    monkeypatch.setattr(storage, "newest_esi_freshness_success_at", lambda: "2026-01-01T00:00:00+00:00")
    out = admin.do_get_health_detail()
    assert out["newest_backup_at"] == created
    assert 7000 < out["newest_backup_age_seconds"] < 7400
    assert out["newest_esi_sync_at"] == "2026-01-01T00:00:00+00:00"
    assert out["scheduler_running"] is False
    assert out["version"]


def test_foreign_request_id_with_control_characters_is_replaced(monkeypatch):
    monkeypatch.setattr(storage, "connect_unscoped", _ok_connect)
    resp = client.get("/api/health", headers={"X-Request-ID": "abc\tdef 12345 \x7f"})
    returned = resp.headers["x-request-id"]
    assert returned != "abc\tdef 12345 \x7f"
    assert len(returned) == 32 and returned.isalnum()


def test_valid_request_id_is_echoed(monkeypatch):
    monkeypatch.setattr(storage, "connect_unscoped", _ok_connect)
    resp = client.get("/api/health", headers={"X-Request-ID": "req-12345678"})
    assert resp.headers["x-request-id"] == "req-12345678"


@pytest.mark.parametrize("bad", ["short", "x" * 65, "has space 1234", "new\nline-1234", "ünïcode-1234"])
def test_accept_or_generate_rejects_invalid_ids(bad):
    assert accept_or_generate(bad) != bad


def test_request_id_is_set_during_the_request_and_appears_in_the_log(monkeypatch):
    lines = []

    class _Capture(logging.Handler):
        def emit(self, record):
            lines.append(f"[{record.request_id}] {record.getMessage()}")

    handler = _Capture()
    handler.addFilter(RequestIdFilter())
    logger = logging.getLogger("eve_trader.api.routers.health")
    logger.addHandler(handler)

    @contextmanager
    def broken(timeout=None):
        raise RuntimeError("down")
        yield  # pragma: no cover

    monkeypatch.setattr(storage, "connect_unscoped", broken)
    try:
        resp = client.get("/api/health", headers={"X-Request-ID": "trace-0000001"})
    finally:
        logger.removeHandler(handler)
    assert resp.headers["x-request-id"] == "trace-0000001"
    assert lines and lines[0].startswith("[trace-0000001] health check degraded")
    # Outside a request the filter falls back to "-".
    assert request_id_var.get() == "-"


def test_request_id_reaches_the_gate_rejection_too():
    # The middleware is outermost, so even a gate 401 carries the header.
    from eve_trader.config import ACCESS_CONFIG

    ACCESS_CONFIG.access_gate_enabled = True
    try:
        resp = client.get("/api/trading/settings")
    finally:
        ACCESS_CONFIG.access_gate_enabled = False
    assert resp.status_code == 401
    assert "x-request-id" in resp.headers

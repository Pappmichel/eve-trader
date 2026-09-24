"""F-19: OpenAPI/docs are gated when the access gate is on."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG

from . import pg_helpers
from .pg_helpers import _apply_admin_schema, _apply_phase1_schema, _apply_phase3_schema  # noqa: F401

client = TestClient(create_app())

_DOCS = ("/docs", "/redoc", "/openapi.json")


def test_docs_are_public_when_gate_is_off(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    for path in _DOCS:
        assert client.get(path).status_code == 200


def test_docs_are_401_when_gate_on_and_unauthenticated(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    for path in _DOCS:
        assert client.get(path).status_code == 401


@pg_helpers.postgres_required()
def test_docs_are_reachable_when_authenticated(
    monkeypatch, _apply_phase1_schema, _apply_phase3_schema, _apply_admin_schema,
):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    pg_helpers.wipe_tables("access_allowlist", "access_requests")
    tenant_id = storage.create_tenant("Docs")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="Docs")
    token = access_gate.create_session_token(11, "Docs", tenant_id)
    cookies = {access_gate.SESSION_COOKIE_NAME: token}
    for path in _DOCS:
        assert client.get(path, cookies=cookies).status_code == 200


@pg_helpers.postgres_required()
def test_openapi_schema_has_no_secrets(
    monkeypatch, _apply_phase1_schema, _apply_phase3_schema, _apply_admin_schema,
):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    pg_helpers.wipe_tables("access_allowlist", "access_requests")
    tenant_id = storage.create_tenant("Docs")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="Docs")
    token = access_gate.create_session_token(11, "Docs", tenant_id)
    body = client.get("/openapi.json", cookies={access_gate.SESSION_COOKIE_NAME: token}).json()
    dumped = str(body).lower()
    assert "session_secret" not in dumped
    assert "password" not in dumped
    assert "refresh_token" not in dumped

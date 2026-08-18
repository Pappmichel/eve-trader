"""Unit tests for access_gate.py - session cookie signing and
resolve_corp_alliance. Router-level tests (login flow, middleware
enforcement) live in tests/test_gate_router.py. Tenant-resolution tests
(storage.resolve_tenant_id, the Postgres-backed replacement for the old
in-memory AccessConfig allowlist - see access_gate.py's module docstring)
live in tests/test_tenant_registry.py.
"""
import pytest

from eve_trader import access_gate
from eve_trader.config import OAuthConfig


@pytest.fixture
def cfg():
    return OAuthConfig(session_secret_key="test-secret-key")


def test_create_and_read_session_token_round_trips(cfg):
    token = access_gate.create_session_token(2112625428, "Some Character", "tenant-abc", cfg)
    data = access_gate.read_session_token(token, cfg)

    assert data == {"character_id": 2112625428, "character_name": "Some Character", "tenant_id": "tenant-abc"}


def test_read_session_token_rejects_tampered_token(cfg):
    token = access_gate.create_session_token(1, "A", "tenant-abc", cfg)
    tampered = token[:-1] + ("x" if token[-1] != "x" else "y")

    assert access_gate.read_session_token(tampered, cfg) is None


def test_read_session_token_rejects_token_signed_with_a_different_key(cfg):
    token = access_gate.create_session_token(1, "A", "tenant-abc", cfg)
    other_cfg = OAuthConfig(session_secret_key="a-completely-different-key")

    assert access_gate.read_session_token(token, other_cfg) is None


def test_read_session_token_rejects_expired_token(cfg, monkeypatch):
    token = access_gate.create_session_token(1, "A", "tenant-abc", cfg)
    monkeypatch.setattr(access_gate, "SESSION_MAX_AGE_SECONDS", -1)  # already "expired"

    assert access_gate.read_session_token(token, cfg) is None


def test_serializer_refuses_to_operate_without_a_session_secret_key():
    cfg = OAuthConfig(session_secret_key="")
    with pytest.raises(RuntimeError, match="SESSION_SECRET_KEY"):
        access_gate.create_session_token(1, "A", "tenant-abc", cfg)


def test_set_session_cookie_is_not_secure_over_plain_http(cfg):
    # Real bug confirmed 2026-08-16: a deployment on a bare IP with no domain
    # yet (no Let's Encrypt cert possible without one) is non-localhost but
    # still plain HTTP - Secure=True there would make the browser silently
    # refuse to ever send the cookie back, breaking login with no visible
    # error. Secure must follow frontend_origin's scheme, not just "is this
    # localhost".
    from fastapi import Response
    cfg.frontend_origin = "http://192.0.2.10"
    response = Response()

    access_gate.set_session_cookie(response, 1, "A", "tenant-abc", cfg)

    assert "secure" not in response.headers["set-cookie"].lower()


def test_set_session_cookie_is_secure_over_https(cfg):
    from fastapi import Response
    cfg.frontend_origin = "https://eve-trader.example.com"
    response = Response()

    access_gate.set_session_cookie(response, 1, "A", "tenant-abc", cfg)

    assert "secure" in response.headers["set-cookie"].lower()


def test_resolve_corp_alliance_happy_path(monkeypatch):
    from eve_trader.esi_client import ESIClient

    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 100})
    monkeypatch.setattr(ESIClient, "corporation_public_info", lambda self, cid: {"alliance_id": 900, "name": "Corp"})

    corporation_id, alliance_id = access_gate.resolve_corp_alliance(1)

    assert corporation_id == 100
    assert alliance_id == 900


def test_resolve_corp_alliance_degrades_gracefully_when_alliance_lookup_fails(monkeypatch):
    from eve_trader.esi_client import ESIClient

    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 100})

    def _raise(self, cid):
        raise RuntimeError("ESI hiccup")
    monkeypatch.setattr(ESIClient, "corporation_public_info", _raise)

    corporation_id, alliance_id = access_gate.resolve_corp_alliance(1)

    assert corporation_id == 100
    assert alliance_id is None  # degraded, not raised

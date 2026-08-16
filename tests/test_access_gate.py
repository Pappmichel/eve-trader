"""Unit tests for access_gate.py - session cookie signing and allowlist
matching. Router-level tests (login flow, middleware enforcement) live in
tests/test_gate_router.py.
"""
import pytest

from eve_trader import access_gate
from eve_trader.config import AccessConfig, OAuthConfig


@pytest.fixture
def cfg():
    return OAuthConfig(session_secret_key="test-secret-key")


def test_create_and_read_session_token_round_trips(cfg):
    token = access_gate.create_session_token(2112625428, "Some Character", cfg)
    data = access_gate.read_session_token(token, cfg)

    assert data == {"character_id": 2112625428, "character_name": "Some Character"}


def test_read_session_token_rejects_tampered_token(cfg):
    token = access_gate.create_session_token(1, "A", cfg)
    tampered = token[:-1] + ("x" if token[-1] != "x" else "y")

    assert access_gate.read_session_token(tampered, cfg) is None


def test_read_session_token_rejects_token_signed_with_a_different_key(cfg):
    token = access_gate.create_session_token(1, "A", cfg)
    other_cfg = OAuthConfig(session_secret_key="a-completely-different-key")

    assert access_gate.read_session_token(token, other_cfg) is None


def test_read_session_token_rejects_expired_token(cfg, monkeypatch):
    token = access_gate.create_session_token(1, "A", cfg)
    monkeypatch.setattr(access_gate, "SESSION_MAX_AGE_SECONDS", -1)  # already "expired"

    assert access_gate.read_session_token(token, cfg) is None


def test_serializer_refuses_to_operate_without_a_session_secret_key():
    cfg = OAuthConfig(session_secret_key="")
    with pytest.raises(RuntimeError, match="SESSION_SECRET_KEY"):
        access_gate.create_session_token(1, "A", cfg)


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

    access_gate.set_session_cookie(response, 1, "A", cfg)

    assert "secure" not in response.headers["set-cookie"].lower()


def test_set_session_cookie_is_secure_over_https(cfg):
    from fastapi import Response
    cfg.frontend_origin = "https://eve-trader.example.com"
    response = Response()

    access_gate.set_session_cookie(response, 1, "A", cfg)

    assert "secure" in response.headers["set-cookie"].lower()


def test_is_allowed_matches_on_character_id():
    cfg = AccessConfig(allowed_character_ids=[42])
    assert access_gate.is_allowed(42, None, None, cfg) is True
    assert access_gate.is_allowed(43, None, None, cfg) is False


def test_is_allowed_matches_on_corporation_id():
    cfg = AccessConfig(allowed_corporation_ids=[100])
    assert access_gate.is_allowed(1, 100, None, cfg) is True
    assert access_gate.is_allowed(1, 101, None, cfg) is False


def test_is_allowed_matches_on_alliance_id():
    cfg = AccessConfig(allowed_alliance_ids=[900])
    assert access_gate.is_allowed(1, 100, 900, cfg) is True
    assert access_gate.is_allowed(1, 100, 901, cfg) is False


def test_is_allowed_any_of_the_three_lists_is_sufficient():
    # Real user request (2026-08-16): all three levels should work together,
    # not just one - e.g. one extra character allowed outside an otherwise-
    # allowed alliance.
    cfg = AccessConfig(allowed_character_ids=[7], allowed_alliance_ids=[900])
    assert access_gate.is_allowed(7, None, None, cfg) is True     # via character list
    assert access_gate.is_allowed(1, None, 900, cfg) is True      # via alliance list
    assert access_gate.is_allowed(1, None, 901, cfg) is False     # matches neither


def test_is_allowed_empty_allowlists_deny_everyone():
    cfg = AccessConfig()
    assert access_gate.is_allowed(1, 2, 3, cfg) is False


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

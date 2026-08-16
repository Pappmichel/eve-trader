"""Router + middleware tests for the access gate: /api/gate/status,
/api/gate/logout, AccessGateMiddleware's enforcement (enabled/disabled), and
auth.py's callback() "gate" branch (allowed/denied). See
tests/test_access_gate.py for the underlying cookie/allowlist unit tests.
"""
import time

import requests
from fastapi.testclient import TestClient

from eve_trader import access_gate
from eve_trader.api.app import create_app
from eve_trader.api.routers import auth as auth_router
from eve_trader.auth import TokenManager
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG

client = TestClient(create_app())


def _enable_gate(monkeypatch, **overrides):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    for key in ("allowed_character_ids", "allowed_corporation_ids", "allowed_alliance_ids"):
        monkeypatch.setattr(ACCESS_CONFIG, key, overrides.get(key, []))


def _session_cookie(character_id: int = 1, character_name: str = "Some Character") -> dict:
    token = access_gate.create_session_token(character_id, character_name, OAUTH_CONFIG)
    return {access_gate.SESSION_COOKIE_NAME: token}


# --------------------------------------------------------------- /gate/status
def test_status_when_gate_disabled_reports_disabled(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)

    resp = client.get("/api/gate/status")

    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "logged_in": False, "character_name": None}


def test_status_with_no_cookie_reports_logged_out(monkeypatch):
    _enable_gate(monkeypatch)

    resp = client.get("/api/gate/status")

    assert resp.json()["logged_in"] is False
    assert resp.json()["character_name"] is None


def test_status_with_valid_cookie_reports_logged_in(monkeypatch):
    _enable_gate(monkeypatch)

    resp = client.get("/api/gate/status", cookies=_session_cookie(character_name="Test Character"))

    assert resp.json() == {"enabled": True, "logged_in": True, "character_name": "Test Character"}


def test_status_with_garbage_cookie_reports_logged_out_not_500(monkeypatch):
    _enable_gate(monkeypatch)

    resp = client.get("/api/gate/status", cookies={access_gate.SESSION_COOKIE_NAME: "not-a-real-token"})

    assert resp.status_code == 200
    assert resp.json()["logged_in"] is False


# --------------------------------------------------------------- /gate/logout
def test_logout_clears_the_session_cookie(monkeypatch):
    _enable_gate(monkeypatch)

    resp = client.post("/api/gate/logout", cookies=_session_cookie())

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    set_cookie = resp.headers.get("set-cookie", "")
    assert access_gate.SESSION_COOKIE_NAME in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


# --------------------------------------------------------------------- middleware
def test_middleware_is_a_no_op_when_gate_disabled(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)

    resp = client.get("/api/trading/settings")

    assert resp.status_code == 200  # no cookie at all, still reachable


def test_middleware_blocks_protected_routes_without_a_session_when_enabled(monkeypatch):
    _enable_gate(monkeypatch)

    resp = client.get("/api/trading/settings")

    assert resp.status_code == 401


def test_middleware_allows_protected_routes_with_a_valid_session_when_enabled(monkeypatch):
    _enable_gate(monkeypatch)

    resp = client.get("/api/trading/settings", cookies=_session_cookie())

    assert resp.status_code == 200


def test_middleware_exempts_the_gate_login_and_status_endpoints_even_when_enabled(monkeypatch):
    _enable_gate(monkeypatch)

    assert client.get("/api/gate/status").status_code == 200
    assert client.post("/api/gate/logout").status_code == 200
    # /api/auth/gate/start builds a real EVE SSO URL and doesn't need
    # network access or a client_id to prove it's *reachable* (not 401'd) -
    # a missing client_id is a distinct 500, still not a 401.
    assert client.get("/api/auth/gate/start").status_code != 401


# --------------------------------------------------- auth.py callback() gate branch
def test_callback_gate_branch_allowed_character_sets_cookie_and_redirects_success(monkeypatch):
    _enable_gate(monkeypatch, allowed_character_ids=[2112625428])
    state = "test-gate-allowed"
    auth_router._pending[state] = {"verifier": "v", "role_prefix": "gate", "scopes": [], "created_at": time.time()}
    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (2112625428, "Allowed Character")))
    # auth.py did `from ...access_gate import resolve_corp_alliance` (a direct
    # name import) - patch the name as bound in auth_router's own namespace,
    # not access_gate's, or auth.py's callback() would keep calling the original.
    monkeypatch.setattr(auth_router, "resolve_corp_alliance", lambda character_id: (100, None))

    resp = client.get("/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert "gate=success" in resp.headers["location"]
    assert access_gate.SESSION_COOKIE_NAME in resp.headers.get("set-cookie", "")


def test_callback_gate_branch_denied_character_redirects_without_a_cookie(monkeypatch):
    _enable_gate(monkeypatch)  # empty allowlists - nobody is allowed
    state = "test-gate-denied"
    auth_router._pending[state] = {"verifier": "v", "role_prefix": "gate", "scopes": [], "created_at": time.time()}
    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (999, "Denied Character")))
    monkeypatch.setattr(auth_router, "resolve_corp_alliance", lambda character_id: (None, None))

    resp = client.get("/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert "gate=denied" in resp.headers["location"]
    assert access_gate.SESSION_COOKIE_NAME not in resp.headers.get("set-cookie", "")


def test_callback_gate_branch_corp_alliance_lookup_failure_still_allows_a_character_match(monkeypatch):
    # Confirmed-by-design degrade: resolve_corp_alliance failing shouldn't
    # block a character-level allowlist entry (see auth.py's callback()).
    _enable_gate(monkeypatch, allowed_character_ids=[2112625428])
    state = "test-gate-esi-hiccup"
    auth_router._pending[state] = {"verifier": "v", "role_prefix": "gate", "scopes": [], "created_at": time.time()}
    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (2112625428, "Allowed Character")))

    def _raise(character_id):
        raise requests.ConnectionError("ESI hiccup")
    monkeypatch.setattr(auth_router, "resolve_corp_alliance", _raise)

    resp = client.get("/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False)

    assert "gate=success" in resp.headers["location"]

"""Router + middleware tests for the access gate: /api/gate/status,
/api/gate/logout, AccessGateMiddleware's enforcement (enabled/disabled),
auth.py's callback() "gate" branch (allowed/denied), and (bottom of the
file) /start + /callback's tenant-id threading for the buyer/seller/
producer branches. See tests/test_access_gate.py for the underlying cookie
unit tests, tests/test_tenant_registry.py for storage.resolve_tenant_id's
own coverage, and tests/test_token_manager.py for TokenManager's own
Postgres-backed coverage.

Most tests here never touch Postgres - a session cookie carries its
tenant_id directly (signed, not looked up), and the endpoints exercised
below (settings GET, gate status/logout) don't query storage. Only the
test_callback_gate_branch_*/test_start_login_*/test_callback_buyer_branch_*
tests touch real Postgres (that's the thing being tested in each case), so
only those are marked postgres_required.
"""
import http.cookies
import time
import urllib.parse
import uuid

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.api.routers import auth as auth_router
from eve_trader.auth import TokenManager
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
    _apply_role_consent_schema, tenant_pair,
)

client = TestClient(create_app())


def _enable_gate(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")


_DEFAULT_TEST_TENANT_ID = "00000000-0000-0000-0000-000000000abc"  # must be a real UUID -
# tenant_scope.enter_tenant (Phase 4) resolves TRADING_CONFIG/PRODUCTION_CONFIG for
# whatever tenant a valid cookie carries, via a real ::uuid-cast Postgres query


def _session_cookie(character_id: int = 1, character_name: str = "Some Character",
                     tenant_id: str = _DEFAULT_TEST_TENANT_ID) -> dict:
    token = access_gate.create_session_token(character_id, character_name, tenant_id)
    return {access_gate.SESSION_COOKIE_NAME: token}


@pytest.fixture
def _wipe_registry():
    """Only requested by the 3 postgres_required callback tests below - a
    real DELETE against tenant_registry_entries, run both before and after
    so a leftover row from a previous run/test can't make
    resolve_tenant_id match unexpectedly."""
    pg_helpers.wipe_tables("tenant_registry_entries")
    yield
    pg_helpers.wipe_tables("tenant_registry_entries")


@pytest.fixture(autouse=True)
def _wipe_tool_grants():
    # Autouse (unlike _wipe_registry above) - several tests in this file set
    # a real tool_grants row for character_id=1 (the _session_cookie default)
    # under _DEFAULT_TEST_TENANT_ID; without wiping between tests, a grant
    # set by one test would silently let a later "missing grant" test pass
    # through instead of 403ing as expected. tenant_role_consents has the
    # exact same problem for the consent round-trip tests below (a POST in
    # one test would leave an "already acknowledged" row a later test's own
    # "before" assertion would then see instead of a clean slate) - wiped
    # here too rather than as its own separate fixture.
    if pg_helpers._postgres_available():
        pg_helpers.wipe_tables("tool_grants", "tenant_role_consents")
    yield


# --------------------------------------------------------------- /gate/status
def test_status_when_gate_disabled_reports_disabled(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)

    resp = client.get("/api/gate/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": False, "logged_in": False, "character_name": None, "tools": list(access_gate.ALL_TOOL_KEYS),
    }


def test_status_with_no_cookie_reports_logged_out(monkeypatch):
    _enable_gate(monkeypatch)

    resp = client.get("/api/gate/status")

    assert resp.json()["logged_in"] is False
    assert resp.json()["character_name"] is None
    assert resp.json()["tools"] == []


@pg_helpers.postgres_required()
def test_status_with_valid_cookie_reports_logged_in(monkeypatch, _apply_phase1_schema, _apply_admin_schema):
    _enable_gate(monkeypatch)
    storage.set_tool_grant(1, "trading", _DEFAULT_TEST_TENANT_ID)

    resp = client.get("/api/gate/status", cookies=_session_cookie(character_name="Test Character"))

    assert resp.json() == {
        "enabled": True, "logged_in": True, "character_name": "Test Character", "tools": ["trading"],
    }


@pg_helpers.postgres_required()
def test_status_default_tenant_reports_every_tool(monkeypatch, _apply_phase1_schema, _apply_admin_schema):
    _enable_gate(monkeypatch)

    resp = client.get("/api/gate/status", cookies=_session_cookie(tenant_id=storage.DEFAULT_TENANT_ID))

    assert resp.json()["tools"] == list(access_gate.ALL_TOOL_KEYS)


def test_status_with_garbage_cookie_reports_logged_out_not_500(monkeypatch):
    _enable_gate(monkeypatch)

    resp = client.get("/api/gate/status", cookies={access_gate.SESSION_COOKIE_NAME: "not-a-real-token"})

    assert resp.status_code == 200
    assert resp.json()["logged_in"] is False
    assert resp.json()["tools"] == []


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


@pg_helpers.postgres_required()
def test_middleware_allows_protected_routes_with_a_valid_session_when_enabled(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema
):
    # tenant_scope.enter_tenant (Phase 4) resolves this tenant's config via a
    # real Postgres query on every request now, not just storage's own
    # ambient tenant - this test genuinely needs Postgres reachable. A valid
    # session alone isn't enough anymore (see access_gate.tools_for) - the
    # cookie's own character_id/tenant_id must also have a "trading" grant.
    _enable_gate(monkeypatch)
    storage.set_tool_grant(1, "trading", _DEFAULT_TEST_TENANT_ID)

    resp = client.get("/api/trading/settings", cookies=_session_cookie())

    assert resp.status_code == 200


@pg_helpers.postgres_required()
def test_middleware_rejects_a_valid_session_missing_the_required_tool_grant(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema
):
    # The real enforcement point - a valid, correctly-signed session with no
    # matching tool_grants row must be refused, not just filtered out of the
    # frontend's Landing page (hiding a card doesn't stop a direct API call).
    _enable_gate(monkeypatch)

    resp = client.get("/api/trading/settings", cookies=_session_cookie())

    assert resp.status_code == 403


@pg_helpers.postgres_required()
def test_middleware_default_tenant_bypasses_the_tool_grant_check(monkeypatch, _apply_phase1_schema, _apply_phase2_schema):
    # storage.DEFAULT_TENANT_ID's own users get every tool without an
    # explicit tool_grants row (see access_gate.tools_for).
    _enable_gate(monkeypatch)

    resp = client.get("/api/trading/settings", cookies=_session_cookie(tenant_id=storage.DEFAULT_TENANT_ID))

    assert resp.status_code == 200


def test_middleware_rejects_not_500s_a_cookie_from_before_tenant_id_existed(monkeypatch):
    # Confirmed real gap: a still-valid, correctly-signed cookie from before
    # tenant_id was added to the session payload (Phase 3a) decodes fine
    # (itsdangerous only checks signature/expiry, not payload shape) but has
    # no "tenant_id" key - must be treated as "not authenticated", not let
    # `data["tenant_id"]` raise KeyError into an unhandled 500.
    _enable_gate(monkeypatch)
    old_format_token = access_gate._serializer(OAUTH_CONFIG).dumps(
        {"character_id": 1, "character_name": "Old Format"}
    )

    resp = client.get("/api/trading/settings",
                       cookies={access_gate.SESSION_COOKIE_NAME: old_format_token})

    assert resp.status_code == 401


def test_middleware_exempts_the_gate_login_and_status_endpoints_even_when_enabled(monkeypatch):
    _enable_gate(monkeypatch)

    assert client.get("/api/gate/status").status_code == 200
    assert client.post("/api/gate/logout").status_code == 200
    # /api/auth/gate/start builds a real EVE SSO URL and doesn't need
    # network access or a client_id to prove it's *reachable* (not 401'd) -
    # a missing client_id is a distinct 500, still not a 401.
    assert client.get("/api/auth/gate/start").status_code != 401


# --------------------------------------------------- auth.py callback() gate branch
@pg_helpers.postgres_required()
def test_callback_gate_branch_allowed_character_sets_cookie_and_redirects_success(
    monkeypatch, _wipe_registry, _apply_phase1_schema, _apply_phase3_schema
):
    _enable_gate(monkeypatch)
    tenant_id = str(uuid.uuid4())
    storage.add_tenant_registry_entry(tenant_id, 2112625428)
    state = "test-gate-allowed"
    auth_router._pending[state] = {"verifier": "v", "role_prefix": "gate", "scopes": [], "created_at": time.time()}
    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (2112625428, "Allowed Character")))

    resp = client.get("/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert "gate=success" in resp.headers["location"]
    set_cookie = resp.headers.get("set-cookie", "")
    assert access_gate.SESSION_COOKIE_NAME in set_cookie
    token = http.cookies.SimpleCookie(set_cookie)[access_gate.SESSION_COOKIE_NAME].value
    data = access_gate.read_session_token(token)
    assert data["tenant_id"] == tenant_id


@pg_helpers.postgres_required()
def test_callback_gate_branch_refreshes_the_cached_character_name(
    monkeypatch, _wipe_registry, _apply_phase1_schema, _apply_phase3_schema
):
    # character_name is cached on tenant_registry_entries (docs/admin_
    # schema.sql) for the Admin UI's user list - a gate login should keep it
    # current (e.g. after an in-game character rename) at no extra ESI cost,
    # since EVE SSO's own _verify() already returns the current name.
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant(f"Test Tenant {uuid.uuid4()}")
    storage.add_tenant_registry_entry(tenant_id, 2112625428, character_name="Old Name")
    state = "test-gate-name-refresh"
    auth_router._pending[state] = {"verifier": "v", "role_prefix": "gate", "scopes": [], "created_at": time.time()}
    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (2112625428, "New Name")))

    client.get("/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False)

    users = storage.list_users_with_grants()
    assert next(u for u in users if u["character_id"] == 2112625428)["character_name"] == "New Name"


@pg_helpers.postgres_required()
def test_callback_gate_branch_denied_character_redirects_without_a_cookie(
    monkeypatch, _wipe_registry, _apply_phase1_schema, _apply_phase3_schema
):
    _enable_gate(monkeypatch)  # no registry entries - nobody resolves to a tenant
    state = "test-gate-denied"
    auth_router._pending[state] = {"verifier": "v", "role_prefix": "gate", "scopes": [], "created_at": time.time()}
    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (999, "Denied Character")))

    resp = client.get("/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert "gate=denied" in resp.headers["location"]
    assert access_gate.SESSION_COOKIE_NAME not in resp.headers.get("set-cookie", "")


@pg_helpers.postgres_required()
def test_middleware_rejects_auth_start_missing_the_required_tool_grant(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema
):
    # GitHub issue #57 (found in a full-codebase audit 2026-08-21, confirmed
    # real gap): /api/auth/{role_prefix}/start used to be reachable by any
    # character with a valid gate session regardless of tool grants - a
    # character granted only "trading" could still register a live ESI
    # token for Production via /api/auth/producer/start. No tool_grants row
    # at all here, so this must 403 the same way a direct
    # /api/production/* call already does.
    _enable_gate(monkeypatch)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")

    resp = client.get("/api/auth/producer/start", cookies=_session_cookie())

    assert resp.status_code == 403


@pg_helpers.postgres_required()
def test_middleware_allows_auth_start_with_the_required_tool_grant(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema
):
    _enable_gate(monkeypatch)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    storage.set_tool_grant(1, "production", _DEFAULT_TEST_TENANT_ID)

    resp = client.get("/api/auth/producer/start", cookies=_session_cookie())

    assert resp.status_code == 200


@pg_helpers.postgres_required()
def test_middleware_rejects_auth_consent_missing_the_required_tool_grant(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema
):
    # Same gap class as #57's own /start fix, for the newer /consent
    # endpoints (role-login data-access confirmation) - these must not fall
    # through _required_tool_for_path ungated just because they were added
    # after that fix.
    _enable_gate(monkeypatch)

    get_resp = client.get("/api/auth/producer/consent", cookies=_session_cookie())
    post_resp = client.post("/api/auth/producer/consent", cookies=_session_cookie())

    assert get_resp.status_code == 403
    assert post_resp.status_code == 403


@pg_helpers.postgres_required()
def test_middleware_allows_auth_consent_with_the_required_tool_grant(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema
):
    _enable_gate(monkeypatch)
    storage.set_tool_grant(1, "production", _DEFAULT_TEST_TENANT_ID)

    get_resp = client.get("/api/auth/producer/consent", cookies=_session_cookie())
    post_resp = client.post("/api/auth/producer/consent", cookies=_session_cookie())

    assert get_resp.status_code == 200
    assert post_resp.status_code == 200


@pg_helpers.postgres_required()
def test_consent_status_round_trips_get_then_post_then_get(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema, _apply_role_consent_schema
):
    _enable_gate(monkeypatch)
    storage.set_tool_grant(1, "production", _DEFAULT_TEST_TENANT_ID)

    before = client.get("/api/auth/producer/consent", cookies=_session_cookie())
    ack = client.post("/api/auth/producer/consent", cookies=_session_cookie())
    after = client.get("/api/auth/producer/consent", cookies=_session_cookie())

    assert before.json() == {"acknowledged": False}
    assert ack.json() == {"acknowledged": True}
    assert after.json() == {"acknowledged": True}


@pg_helpers.postgres_required()
def test_consent_rejects_the_gate_role_prefix(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema, _apply_role_consent_schema
):
    # "gate" consent is tracked client-side (localStorage) - see
    # role_consent_schema.sql's own comment on why a pre-login tenant can't
    # exist to attach a server-side record to.
    _enable_gate(monkeypatch)

    resp = client.get("/api/auth/gate/consent", cookies=_session_cookie())

    assert resp.status_code == 400


def test_consent_rejects_an_unknown_role_prefix():
    resp = client.get("/api/auth/not-a-real-role/consent")
    assert resp.status_code == 400


def test_start_login_rejects_an_unrecognized_role_prefix(monkeypatch):
    # GitHub issue #57: role_prefix used to be accepted verbatim with no
    # allowlist, becoming a permanent TokenManager role key for an arbitrary
    # string. Gate disabled here - this is start_login's own validation, not
    # the tool-grant check (see the two tests above for that).
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")

    resp = client.get("/api/auth/not-a-real-role/start")

    assert resp.status_code == 400


# --------------------------------- auth.py /start + /callback tenant threading (non-gate)
@pg_helpers.postgres_required()
def test_start_login_captures_the_ambient_tenant_into_pending(monkeypatch):
    # Gate disabled (today's default) - AccessGateMiddleware sets
    # DEFAULT_TENANT_ID unconditionally, and /start must capture exactly
    # that into _pending[state] for /callback (an exempt path with no
    # automatic ambient tenant) to pick back up.
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")

    resp = client.get("/api/auth/buyer/start")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(resp.json()["url"]).query)["state"][0]

    assert auth_router._pending[state]["tenant_id"] == storage.DEFAULT_TENANT_ID


@pg_helpers.postgres_required()
def test_callback_buyer_branch_persists_the_token_under_the_correct_tenant(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema, tenant_pair
):
    # The real fix this session adds: /callback's non-gate branches used to
    # have no tenant context at all (an AccessGateMiddleware-exempt path) -
    # a buyer/seller/producer login must land under the same tenant whose
    # already-authenticated session initiated /start, not some other tenant.
    tenant_a, tenant_b = tenant_pair
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    # GitHub issue #57: /api/auth/buyer/start is tool-gated now (requires
    # "trading", same as every /api/trading/* route) - without this grant
    # the request 403s before ever reaching /start's own logic.
    storage.set_tool_grant(1, "trading", tenant_a)

    start_resp = client.get("/api/auth/buyer/start", cookies=_session_cookie(tenant_id=tenant_a))
    state = urllib.parse.parse_qs(urllib.parse.urlparse(start_resp.json()["url"]).query)["state"][0]

    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (42, "Buyer Char")))

    resp = client.get("/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False)

    assert "auth=success" in resp.headers["location"]
    # GitHub issue #46: buyer/seller are multi-character now - the token
    # lands under "buyer:<char_id>", not a single fixed "buyer" key.
    with storage.tenant_context(tenant_a):
        record = TokenManager().get_record("buyer:42")
        assert record is not None and record.character_id == 42
    with storage.tenant_context(tenant_b):
        assert TokenManager().get_record("buyer:42") is None

"""Phase 6 Characters router + grant isolation.

A session with production but not characters 403s /api/characters/* and
still 200s Production reads. A session with characters can toggle sharing
and fetch a confirm-dialog payload. Prefix /start stays until Phase 9.
"""
from __future__ import annotations

from dataclasses import asdict
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.api.routers import auth as auth_router
from eve_trader.auth import TokenManager, TokenRecord
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_esi_access_schema, _apply_phase1_schema,
    _apply_phase2_schema, _apply_phase3_schema,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

client = TestClient(create_app())

_TENANT = "00000000-0000-0000-0000-000000000abc"
ALICE = 1001


def _enable_gate(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")


def _session_cookie(character_id: int = 1, tenant_id: str = _TENANT) -> dict:
    token = access_gate.create_session_token(character_id, "Some Character", tenant_id)
    return {access_gate.SESSION_COOKIE_NAME: token}


def _provision(tools=(), character_id: int = 1, tenant_id: str = _TENANT):
    storage.add_tenant_registry_entry(tenant_id, character_id, character_name="Some Character")
    for tool in tools:
        storage.set_tool_grant(character_id, tool, tenant_id)


@pytest.fixture(autouse=True)
def _wipe():
    client.cookies.clear()
    pg_helpers.wipe_tables(
        "tool_grants", "tenant_registry_entries", "character_session_revocations",
        "esi_sharing", "tenant_tokens",
    )
    yield
    client.cookies.clear()
    pg_helpers.wipe_tables(
        "tool_grants", "tenant_registry_entries", "character_session_revocations",
        "esi_sharing", "tenant_tokens",
    )


def test_characters_routes_403_without_characters_grant_production_still_200(
    monkeypatch, _apply_admin_schema, _apply_esi_access_schema,
):
    _enable_gate(monkeypatch)
    _provision(tools=("production",))
    cookies = _session_cookie()

    denied = client.get("/api/characters/sharing", cookies=cookies)
    assert denied.status_code == 403

    # stock-targets is a phase1 table (empty is a valid 200). /sde/counts
    # needs sde_type_slots, which CI Postgres does not create.
    prod = client.get("/api/production/stock-targets", cookies=cookies)
    assert prod.status_code == 200
    assert prod.json() == []


def test_characters_grant_can_toggle_sharing_and_read_preview(
    monkeypatch, _apply_admin_schema, _apply_esi_access_schema,
):
    _enable_gate(monkeypatch)
    _provision(tools=("characters", "production"))
    cookies = _session_cookie()

    listed = client.get("/api/characters/sharing", cookies=cookies)
    assert listed.status_code == 200
    assert listed.json() == []

    toggled = client.post("/api/characters/sharing", cookies=cookies, json={
        "owner_type": "character", "owner_id": ALICE,
        "data_kind": "assets", "tool_key": "production", "enabled": True,
    })
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is True

    with storage.tenant_context(_TENANT):
        storage.save_tenant_token(f"producer:{ALICE}", asdict(TokenRecord(
            role=f"producer:{ALICE}", character_id=ALICE, character_name="Alice",
            access_token="a", refresh_token="r", expires_at=9999999999.0,
            scopes="esi-assets.read_assets.v1",
        )))
    preview = client.get(
        f"/api/characters/access-preview?character_id={ALICE}&extra_kinds=wallet",
        cookies=cookies,
    )
    assert preview.status_code == 200
    by_key = {i["key"]: i for i in preview.json()["items"]}
    assert by_key["assets"]["added"] is False
    assert by_key["wallet"]["added"] is True


def test_prefix_start_still_exists_for_sidebar_callers(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    _provision(tools=("production",))
    resp = client.get("/api/auth/producer/start", cookies=_session_cookie())
    assert resp.status_code == 200
    assert "url" in resp.json()


def test_reauth_start_requires_characters_grant(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    _provision(tools=("production",))
    resp = client.get(
        f"/api/characters/reauth/start?character_id={ALICE}&extra_kinds=wallet",
        cookies=_session_cookie(),
    )
    assert resp.status_code == 403


def test_prefix_consent_routes_are_gone(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    _provision(tools=("production",))
    cookies = _session_cookie()
    get_resp = client.get("/api/auth/producer/consent", cookies=cookies)
    post_resp = client.post("/api/auth/producer/consent", cookies=cookies)
    assert get_resp.status_code == 404
    assert post_resp.status_code == 404


def test_reauth_callback_writes_via_reauth_write_role_and_rejects_mismatch(
    monkeypatch, _apply_admin_schema,
):
    _enable_gate(monkeypatch)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    _provision(tools=("characters",))

    with storage.tenant_context(_TENANT):
        storage.save_tenant_token(f"producer:{ALICE}", asdict(TokenRecord(
            role=f"producer:{ALICE}", character_id=ALICE, character_name="Alice",
            access_token="a", refresh_token="r", expires_at=9999999999.0,
            scopes="esi-assets.read_assets.v1",
        )))
        storage.save_tenant_token(f"doctrine-assets:{ALICE}", asdict(TokenRecord(
            role=f"doctrine-assets:{ALICE}", character_id=ALICE, character_name="Alice",
            access_token="a", refresh_token="r", expires_at=9999999999.0,
            scopes="esi-assets.read_assets.v1",
        )))

    start = client.get(
        f"/api/characters/reauth/start?character_id={ALICE}&extra_kinds=wallet",
        cookies=_session_cookie(),
    )
    assert start.status_code == 200
    state = urllib.parse.parse_qs(urllib.parse.urlparse(start.json()["url"]).query)["state"][0]
    assert auth_router._pending[state]["reauth_character_id"] == ALICE

    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (9999, "Wrong Char")))
    mismatch = client.get(
        "/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False,
    )
    assert "character_mismatch" in mismatch.headers["location"]

    start = client.get(
        f"/api/characters/reauth/start?character_id={ALICE}&extra_kinds=wallet",
        cookies=_session_cookie(),
    )
    state = urllib.parse.parse_qs(urllib.parse.urlparse(start.json()["url"]).query)["state"][0]
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (ALICE, "Alice")))
    ok = client.get(
        "/api/auth/callback", params={"code": "abc", "state": state}, follow_redirects=False,
    )
    assert "auth=success" in ok.headers["location"]
    with storage.tenant_context(_TENANT):
        kept = TokenManager().get_record(f"doctrine-assets:{ALICE}")
        assert kept is not None
        assert "esi-wallet.read_character_wallet.v1" in kept.scopes
        assert TokenManager().get_record(f"producer:{ALICE}") is None

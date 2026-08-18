"""Tests for auth.py's TokenManager, cut over to Postgres (tenant_tokens) in
the multi-tenant migration's Phase 3b - see docs/MULTI_TENANT_PLAN.md.
"""
import json

import pytest

from eve_trader import storage
from eve_trader.auth import TokenManager, TokenRecord, import_tokens_file
from eve_trader.config import OAuthConfig

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, tenant, tenant_pair  # noqa: F401


def _record(role: str, character_id: int = 1, character_name: str = "Test Char") -> TokenRecord:
    return TokenRecord(
        role=role, character_id=character_id, character_name=character_name,
        access_token="access", refresh_token="refresh", expires_at=9999999999.0, scopes="",
    )


def test_construction_does_not_touch_storage():
    # No ambient tenant set at all (no `tenant` fixture requested) - proves
    # __init__ itself is now storage-free (lazy-loaded on first real access,
    # not eager at construction), unlike the old file-based version.
    TokenManager(OAuthConfig())


@pg_helpers.postgres_required()
def test_first_real_access_without_an_ambient_tenant_fails_closed():
    # The flip side of the above - construction alone is safe with no
    # tenant, but the first real storage-touching call still fails closed,
    # same as every other storage.py entry point post-Phase-1.
    tm = TokenManager(OAuthConfig())
    with pytest.raises(RuntimeError, match="no current tenant set"):
        tm.has_token("buyer")


@pg_helpers.postgres_required()
def test_save_record_round_trips_through_a_fresh_instance(tenant):
    tm = TokenManager(OAuthConfig())
    tm._tokens["buyer"] = _record("buyer", character_id=42, character_name="Jason Andven")
    tm._save_record("buyer")

    fresh = TokenManager(OAuthConfig())
    assert fresh.has_token("buyer")
    record = fresh.get_record("buyer")
    assert record.character_id == 42
    assert record.character_name == "Jason Andven"


@pg_helpers.postgres_required()
def test_remove_token_is_idempotent_even_with_no_stored_row(tenant):
    tm = TokenManager(OAuthConfig())
    tm.remove_token("nonexistent")  # must not raise

    tm._tokens["seller"] = _record("seller")
    tm._save_record("seller")
    tm.remove_token("seller")

    assert TokenManager(OAuthConfig()).has_token("seller") is False


@pg_helpers.postgres_required()
def test_list_roles_returns_only_the_matching_prefix(tenant):
    tm = TokenManager(OAuthConfig())
    for role in ("producer:1", "producer:2", "buyer"):
        tm._tokens[role] = _record(role)
        tm._save_record(role)

    assert sorted(TokenManager(OAuthConfig()).list_roles("producer")) == ["producer:1", "producer:2"]


@pg_helpers.postgres_required()
def test_tokens_are_isolated_per_tenant(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        tm = TokenManager(OAuthConfig())
        tm._tokens["buyer"] = _record("buyer")
        tm._save_record("buyer")

    with storage.tenant_context(tenant_b):
        assert TokenManager(OAuthConfig()).has_token("buyer") is False

    with storage.tenant_context(tenant_a):
        assert TokenManager(OAuthConfig()).has_token("buyer") is True


@pg_helpers.postgres_required()
def test_import_tokens_file_applies_the_legacy_producer_migration(tenant, tmp_path):
    # Real-world shape: a pre-multi-character tokens.json used a single
    # fixed "producer" role - import_tokens_file must re-key it to
    # "producer:<character_id>", same as TokenManager's old on-disk _load()
    # used to, so it doesn't silently vanish on import.
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({
        "buyer": {
            "role": "buyer", "character_id": 1, "character_name": "Buyer Char",
            "access_token": "a", "refresh_token": "r", "expires_at": 9999999999.0, "scopes": "",
        },
        "producer": {
            "role": "producer", "character_id": 77, "character_name": "Producer Char",
            "access_token": "a", "refresh_token": "r", "expires_at": 9999999999.0, "scopes": "",
        },
    }))

    count = import_tokens_file(tenant, path)

    assert count == 2
    tm = TokenManager(OAuthConfig())
    assert tm.has_token("buyer")
    assert tm.has_token("producer:77")
    assert tm.has_token("producer") is False


@pg_helpers.postgres_required()
def test_import_tokens_file_is_safe_to_rerun(tenant, tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({
        "seller": {
            "role": "seller", "character_id": 5, "character_name": "Seller Char",
            "access_token": "a", "refresh_token": "r", "expires_at": 9999999999.0, "scopes": "",
        },
    }))

    first = import_tokens_file(tenant, path)
    second = import_tokens_file(tenant, path)

    assert first == second == 1
    assert TokenManager(OAuthConfig()).get_record("seller").character_name == "Seller Char"

"""Tests for auth.py's TokenManager, cut over to Postgres (tenant_tokens) in
the multi-tenant migration's Phase 3b - see docs/MULTI_TENANT_PLAN.md.
"""
import json
from dataclasses import asdict

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
    # A canonical "prefix:character_id" key - the real production shape,
    # untouched by the P5-07/P5-08 legacy-bare-role migration below (which
    # has its own dedicated tests). Keeps this test about round-tripping,
    # not migration.
    tm = TokenManager(OAuthConfig())
    tm._tokens["buyer:42"] = _record("buyer:42", character_id=42, character_name="Jason Andven")
    tm._save_record("buyer:42")

    fresh = TokenManager(OAuthConfig())
    assert fresh.has_token("buyer:42")
    record = fresh.get_record("buyer:42")
    assert record.character_id == 42
    assert record.character_name == "Jason Andven"


@pg_helpers.postgres_required()
def test_remove_token_is_idempotent_even_with_no_stored_row(tenant):
    tm = TokenManager(OAuthConfig())
    tm.remove_token("nonexistent")  # must not raise

    # Deliberately a bare legacy key, set/saved directly (bypassing _load(),
    # same as remove_token itself never calling it - see remove_token's own
    # docstring for why self-loading here would be wrong).
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

    # "buyer" (bare) is present alongside the two canonical producer keys -
    # list_roles's own prefix filter must not be confused by it, and the
    # fresh instance's own first access silently migrates "buyer" ->
    # "buyer:1" in the background (see the dedicated migration tests below)
    # without affecting this assertion at all.
    assert sorted(TokenManager(OAuthConfig()).list_roles("producer")) == ["producer:1", "producer:2"]


@pg_helpers.postgres_required()
def test_tokens_are_isolated_per_tenant(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        tm = TokenManager(OAuthConfig())
        tm._tokens["buyer:1"] = _record("buyer:1")
        tm._save_record("buyer:1")

    with storage.tenant_context(tenant_b):
        assert TokenManager(OAuthConfig()).has_token("buyer:1") is False

    with storage.tenant_context(tenant_a):
        assert TokenManager(OAuthConfig()).has_token("buyer:1") is True


@pg_helpers.postgres_required()
def test_import_tokens_file_applies_the_legacy_producer_migration(tenant, tmp_path):
    # Real-world shape: a pre-multi-character tokens.json used single fixed
    # "buyer"/"producer" roles - import_tokens_file must re-key BOTH to
    # "<prefix>:<character_id>" (P5-07/P5-08 generalized this from the
    # original producer-only re-key to every role prefix - see
    # _rekey_legacy_bare_roles's own docstring), so neither silently
    # vanishes, and neither is stuck un-removable via the API afterward.
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
    assert tm.has_token("buyer:1")
    assert tm.has_token("producer:77")
    assert tm.has_token("buyer") is False
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
    assert TokenManager(OAuthConfig()).get_record("seller:5").character_name == "Seller Char"
    assert TokenManager(OAuthConfig()).get_record("seller") is None


# --------------------------------------------- P5-07 / P5-08 bare-role migration
# _load() (not just import_tokens_file) self-heals any bare legacy role key
# still sitting in storage from before this migration existed - the storage
# row itself is rewritten, not just this instance's in-memory view, so the
# fix survives across the fresh-TokenManager-per-request pattern this class
# uses everywhere else. See auth._rekey_legacy_bare_roles's own docstring
# for the full rationale (a bare key can never be *removed* via the HTTP API
# once validate_role_key_for_tool's canonical-only grammar shipped - P5-07 -
# and esi_client's per-auth_role caches treat it as one principal when it is
# only unique within a tenant - P5-08).

@pg_helpers.postgres_required()
def test_load_migrates_a_legacy_bare_role_key_in_storage(tenant):
    with storage.tenant_context(tenant):
        storage.save_tenant_token("seller", asdict(_record("seller", character_id=42, character_name="Legacy Seller")))

    tm = TokenManager(OAuthConfig())
    assert tm.has_token("seller") is False
    record = tm.get_record("seller:42")
    assert record is not None
    assert record.character_id == 42
    assert record.character_name == "Legacy Seller"
    assert record.role == "seller:42"

    # Persisted, not just in this instance's memory - a second, independent
    # TokenManager (the normal per-request pattern) must see the same
    # already-migrated storage row, not re-derive it from a stale bare one.
    with storage.tenant_context(tenant):
        stored = storage.load_all_tenant_tokens()
    assert "seller" not in stored
    assert "seller:42" in stored


@pg_helpers.postgres_required()
def test_migrated_role_is_removable_through_remove_token(tenant):
    # The actual P5-07 failure mode: DELETE .../auth/character/seller used
    # to 400 (validate_role_key_for_tool rejects a bare key) with no way to
    # ever reach the token underneath. The real UI flow is list-then-remove
    # (the frontend always echoes back the role_key GET .../characters just
    # returned) - simulate exactly that: load once (as a character-list
    # request would), then remove using the canonical key it produced.
    with storage.tenant_context(tenant):
        storage.save_tenant_token("seller", asdict(_record("seller", character_id=42)))

    tm = TokenManager(OAuthConfig())
    tm._ensure_loaded()  # what a "list my sellers" call does before this
    assert tm.list_roles("seller") == ["seller:42"]

    tm.remove_token("seller:42")

    assert TokenManager(OAuthConfig()).has_token("seller:42") is False
    with storage.tenant_context(tenant):
        assert storage.load_all_tenant_tokens() == {}


@pg_helpers.postgres_required()
def test_load_migration_is_idempotent(tenant):
    with storage.tenant_context(tenant):
        storage.save_tenant_token("producer", asdict(_record("producer", character_id=9)))

    TokenManager(OAuthConfig())._ensure_loaded()  # first load: migrates + persists
    with storage.tenant_context(tenant):
        after_first = storage.load_all_tenant_tokens()

    TokenManager(OAuthConfig())._ensure_loaded()  # second load: nothing left to migrate
    with storage.tenant_context(tenant):
        after_second = storage.load_all_tenant_tokens()

    assert after_first == after_second == {"producer:9": asdict(_record("producer:9", character_id=9))}


@pg_helpers.postgres_required()
def test_load_drops_a_stale_bare_duplicate_when_canonical_already_exists(tenant):
    # A data-inconsistency edge case: both a bare "seller" row AND an
    # already-canonical "seller:42" row exist for the SAME character_id
    # (e.g. a half-completed manual migration). _rekey_legacy_bare_roles
    # must not silently overwrite the live canonical record with the bare
    # one - it drops the stale duplicate instead (mirrors
    # import_tokens_file's own pre-existing "if new_role not in tokens"
    # guard).
    with storage.tenant_context(tenant):
        storage.save_tenant_token("seller", asdict(_record("seller", character_id=42, character_name="Stale")))
        storage.save_tenant_token(
            "seller:42", asdict(_record("seller:42", character_id=42, character_name="Live")),
        )

    tm = TokenManager(OAuthConfig())
    tm._ensure_loaded()

    assert tm.has_token("seller") is False
    assert tm.get_record("seller:42").character_name == "Live"
    with storage.tenant_context(tenant):
        assert set(storage.load_all_tenant_tokens()) == {"seller:42"}


@pg_helpers.postgres_required()
def test_load_migrates_a_bare_role_for_a_different_character_without_colliding(tenant):
    # Bare "seller" (character 42) and canonical "seller:99" (a different,
    # already multi-character-registered character) coexist - both are real
    # distinct characters and must both survive the migration.
    with storage.tenant_context(tenant):
        storage.save_tenant_token("seller", asdict(_record("seller", character_id=42, character_name="Legacy Seller")))
        storage.save_tenant_token(
            "seller:99", asdict(_record("seller:99", character_id=99, character_name="New Seller")),
        )

    tm = TokenManager(OAuthConfig())
    tm._ensure_loaded()

    assert sorted(tm.list_roles("seller")) == ["seller:42", "seller:99"]
    assert tm.get_record("seller:42").character_name == "Legacy Seller"
    assert tm.get_record("seller:99").character_name == "New Seller"

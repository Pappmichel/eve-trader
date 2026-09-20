"""Phase 4 token selector: pick a role key by scope, not by prefix."""
from __future__ import annotations

from dataclasses import asdict

import pytest

from eve_trader import storage
from eve_trader.auth import TokenManager, TokenRecord, validate_role_key
from eve_trader.config import OAuthConfig
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.selector import (
    character_has_token_pool,
    delete_strict_subset_tokens,
    normalize_scopes,
    reauth_write_role,
    select_auth_role,
)

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

ASSETS = "esi-assets.read_assets.v1"
JOBS = "esi-industry.read_character_jobs.v1"
WALLET = "esi-wallet.read_character_wallet.v1"
ALICE = 1001


def _rec(role: str, character_id: int, scopes: str, name: str = "Alice") -> TokenRecord:
    return TokenRecord(
        role=role, character_id=character_id, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=scopes,
    )


def test_normalize_scopes_treats_whitespace_and_dupes_as_the_same_set():
    assert normalize_scopes("b a") == normalize_scopes("a  b a")
    assert normalize_scopes("a b") == frozenset({"a", "b"})
    assert normalize_scopes("") == frozenset()
    assert normalize_scopes("  ") == frozenset()


def test_select_auth_role_treats_normalized_equal_scope_strings_as_equal_sets():
    records = [
        _rec("seller:1001", ALICE, "b a"),
        _rec("buyer:1001", ALICE, "a  b a"),
    ]
    # Same set {a, b}; equal size, so lexically first key wins.
    assert select_auth_role(ALICE, "a", records=records) == "buyer:1001"


def test_larger_overlapping_scope_set_wins():
    records = [
        _rec("producer:1001", ALICE, f"{ASSETS} {JOBS}"),
        _rec("doctrine-assets:1001", ALICE, ASSETS),
    ]
    assert select_auth_role(ALICE, ASSETS, records=records) == "producer:1001"


def test_broad_token_lacking_required_scope_loses_to_narrow_one_that_has_it():
    records = [
        _rec("producer:1001", ALICE, f"{ASSETS} {JOBS}"),
        _rec("seller:1001", ALICE, WALLET),
    ]
    assert select_auth_role(ALICE, WALLET, records=records) == "seller:1001"
    assert select_auth_role(ALICE, ASSETS, records=records) == "producer:1001"


def test_equal_size_sets_pick_lexically_first_role_key():
    records = [
        _rec("seller:1001", ALICE, ASSETS),
        _rec("buyer:1001", ALICE, ASSETS),
    ]
    assert select_auth_role(ALICE, ASSETS, records=records) == "buyer:1001"


def test_same_inputs_return_the_same_role_key_across_100_calls():
    records = [
        _rec("producer:1001", ALICE, f"{ASSETS} {JOBS} {WALLET}"),
        _rec("seller:1001", ALICE, f"{ASSETS} {WALLET}"),
        _rec("buyer:1001", ALICE, WALLET),
    ]
    first = select_auth_role(ALICE, ASSETS, records=records)
    assert first == "producer:1001"
    for _ in range(100):
        assert select_auth_role(ALICE, ASSETS, records=records) == first


def test_no_token_carrying_the_scope_returns_none():
    records = [_rec("producer:1001", ALICE, ASSETS)]
    assert select_auth_role(ALICE, WALLET, records=records) is None
    assert select_auth_role(9999, ASSETS, records=records) is None


def test_reauth_write_role_reuses_lexically_first_existing_key():
    records = [
        _rec("seller:1001", ALICE, WALLET),
        _rec("buyer:1001", ALICE, WALLET),
    ]
    assert reauth_write_role(ALICE, records=records) == "buyer:1001"


def test_reauth_write_role_uses_esi_prefix_when_character_has_no_key():
    assert reauth_write_role(ALICE, records=[]) == "esi:1001"
    assert validate_role_key("esi:1001") == "esi:1001"


def test_orchestrator_no_longer_resolves_tokens_by_prefix():
    assert not hasattr(orchestrator, "_list_token_characters")
    assert not hasattr(orchestrator, "_auth_roles_for")
    assert not hasattr(orchestrator, "TOOL_ROLE_PREFIXES")


def test_pool_hint_is_true_only_for_multi_row_characters():
    alice_two = [
        _rec("producer:1001", ALICE, ASSETS),
        _rec("doctrine-assets:1001", ALICE, ASSETS),
    ]
    bob_one = [_rec("seller:2002", 2002, WALLET)]
    assert character_has_token_pool(ALICE, records=alice_two) is True
    assert character_has_token_pool(2002, records=bob_one) is False
    assert character_has_token_pool(ALICE, records=bob_one) is False


@pytest.fixture(autouse=False)
def _wipe_tokens(tenant):
    pg_helpers.wipe_tables("tenant_tokens")
    yield
    pg_helpers.wipe_tables("tenant_tokens")


@pg_helpers.postgres_required()
def test_superset_write_deletes_strict_subsets(_wipe_tokens):
    storage.save_tenant_token("buyer:1001", asdict(_rec("buyer:1001", ALICE, f"{ASSETS} {JOBS} {WALLET}")))
    storage.save_tenant_token("producer:1001", asdict(_rec("producer:1001", ALICE, f"{ASSETS} {JOBS}")))
    storage.save_tenant_token("seller:2002", asdict(_rec("seller:2002", 2002, WALLET)))

    deleted = delete_strict_subset_tokens(ALICE)
    assert deleted == ["producer:1001"]
    tm = TokenManager(OAuthConfig())
    assert tm.has_token("buyer:1001") is True
    assert tm.has_token("producer:1001") is False
    # Other characters are untouched.
    assert tm.has_token("seller:2002") is True


@pg_helpers.postgres_required()
def test_equal_scope_duplicates_survive_until_a_superset_write(_wipe_tokens):
    storage.save_tenant_token("buyer:1001", asdict(_rec("buyer:1001", ALICE, ASSETS)))
    storage.save_tenant_token("seller:1001", asdict(_rec("seller:1001", ALICE, ASSETS)))

    assert delete_strict_subset_tokens(ALICE) == []
    tm = TokenManager(OAuthConfig())
    assert tm.has_token("buyer:1001") is True
    assert tm.has_token("seller:1001") is True

    storage.save_tenant_token(
        "buyer:1001", asdict(_rec("buyer:1001", ALICE, f"{ASSETS} {WALLET}")),
    )
    deleted = delete_strict_subset_tokens(ALICE)
    assert deleted == ["seller:1001"]
    tm = TokenManager(OAuthConfig())
    assert tm.has_token("buyer:1001") is True
    assert tm.has_token("seller:1001") is False


@pg_helpers.postgres_required()
def test_normalized_equal_scope_strings_are_not_strict_subsets(_wipe_tokens):
    storage.save_tenant_token("buyer:1001", asdict(_rec("buyer:1001", ALICE, "b a")))
    storage.save_tenant_token("seller:1001", asdict(_rec("seller:1001", ALICE, "a  b a")))

    assert delete_strict_subset_tokens(ALICE) == []
    tm = TokenManager(OAuthConfig())
    assert tm.has_token("buyer:1001") is True
    assert tm.has_token("seller:1001") is True

"""F-05: canonical role_key domain."""
from __future__ import annotations

import pytest

from eve_trader.actions import (
    ActionError,
    do_remove_trading_character,
    do_wallet_balance,
    do_wallet_transactions,
)
from eve_trader.auth import (
    InvalidRoleKey,
    ROLE_PREFIX_TOOL,
    TOOL_ROLE_PREFIXES,
    validate_role_key,
    validate_role_key_for_tool,
)
from eve_trader.doctrine.actions import do_remove_doctrine_character
from eve_trader.production.actions import do_remove_producer_character
from eve_trader.station_trading.actions import do_remove_trader_character

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)


VALID = (
    "buyer:1",
    "seller:2112625428",
    "producer:42",
    "doctrine:99",
    "doctrine-assets:99",
    "trader:123456789012345",
    "esi:1001",
)


@pytest.mark.parametrize("key", VALID)
def test_valid_role_keys(key):
    assert validate_role_key(key) == key


@pytest.mark.parametrize("key", [
    "",
    "buyer",
    "buyer:",
    "buyer:0",
    "buyer:-1",
    "buyer:01",
    "BUYER:1",
    "gate:1",
    "admin:1",
    "buyer:1:2",
    "buyer:1/",
    "../buyer:1",
    "buyer:1;DROP TABLE",
    "buyer:1'",
    " buyer:1",
    "buyer:1 ",
    "buyer:1\x00",
    "buyer:1\n",
    "buyer:１",
    "buyer:1/../etc/passwd",
    "x" * 49,
    "trading:1",
])
def test_invalid_role_keys(key):
    with pytest.raises(InvalidRoleKey):
        validate_role_key(key)


def test_non_string_is_invalid():
    with pytest.raises(InvalidRoleKey):
        validate_role_key(12345)  # type: ignore[arg-type]


def test_do_remove_rejects_invalid_role_key():
    with pytest.raises(ActionError, match="Invalid role_key"):
        do_remove_trading_character("../seller:1")


def test_tool_role_prefix_mapping_is_the_login_vocabulary():
    assert TOOL_ROLE_PREFIXES["trading"] == frozenset({"buyer", "seller"})
    assert TOOL_ROLE_PREFIXES["production"] == frozenset({"producer"})
    assert TOOL_ROLE_PREFIXES["doctrine"] == frozenset({"doctrine", "doctrine-assets"})
    assert TOOL_ROLE_PREFIXES["station_trading"] == frozenset({"trader"})
    assert "refining" not in TOOL_ROLE_PREFIXES
    assert ROLE_PREFIX_TOOL["buyer"] == "trading"
    assert ROLE_PREFIX_TOOL["gate"] is None


@pytest.mark.parametrize("key,tool", [
    ("buyer:1", "trading"),
    ("seller:2112625428", "trading"),
    ("producer:42", "production"),
    ("doctrine:99", "doctrine"),
    ("doctrine-assets:99", "doctrine"),
    ("trader:7", "station_trading"),
])
def test_validate_role_key_for_tool_accepts_owned_prefix(key, tool):
    assert validate_role_key_for_tool(key, tool) == key


@pytest.mark.parametrize("key,tool", [
    ("producer:777", "trading"),
    ("doctrine:1", "trading"),
    ("doctrine-assets:1", "trading"),
    ("trader:1", "trading"),
    ("buyer:1", "production"),
    ("seller:1", "production"),
    ("doctrine:1", "production"),
    ("trader:1", "production"),
    ("buyer:1", "doctrine"),
    ("producer:1", "doctrine"),
    ("trader:1", "doctrine"),
    ("buyer:1", "station_trading"),
    ("producer:1", "station_trading"),
    ("doctrine:1", "station_trading"),
    ("buyer:1", "refining"),
    ("ore:1", "trading"),
    ("esi:1", "production"),
    ("esi:1", "trading"),
])
def test_validate_role_key_for_tool_rejects_cross_tool_and_unknown(key, tool):
    with pytest.raises(InvalidRoleKey):
        validate_role_key_for_tool(key, tool)


def test_trading_remove_rejects_producer_without_deleting(monkeypatch):
    called = []
    monkeypatch.setattr(
        "eve_trader.actions.TokenManager.remove_token",
        lambda self, role: called.append(role),
    )
    with pytest.raises(ActionError, match="Invalid role_key"):
        do_remove_trading_character("producer:777")
    assert called == []


def test_trading_remove_accepts_seller(monkeypatch):
    called = []
    monkeypatch.setattr(
        "eve_trader.actions.TokenManager.remove_token",
        lambda self, role: called.append(role),
    )
    assert do_remove_trading_character("seller:1") == {"removed": "seller:1"}
    assert called == ["seller:1"]


def test_production_remove_rejects_buyer(monkeypatch):
    called = []
    monkeypatch.setattr(
        "eve_trader.production.actions.TokenManager.remove_token",
        lambda self, role: called.append(role),
    )
    with pytest.raises(ActionError, match="Invalid role_key"):
        do_remove_producer_character("buyer:1")
    assert called == []


def test_doctrine_remove_rejects_trader(monkeypatch):
    called = []
    monkeypatch.setattr(
        "eve_trader.doctrine.actions.TokenManager.remove_token",
        lambda self, role: called.append(role),
    )
    with pytest.raises(ActionError, match="Invalid role_key"):
        do_remove_doctrine_character("trader:1")
    assert called == []


def test_station_trading_remove_rejects_producer(monkeypatch):
    called = []
    monkeypatch.setattr(
        "eve_trader.station_trading.actions.TokenManager.remove_token",
        lambda self, role: called.append(role),
    )
    with pytest.raises(ActionError, match="Invalid role_key"):
        do_remove_trader_character("producer:1")
    assert called == []


def test_wallet_actions_reject_other_tool_role_without_lookup(monkeypatch):
    looked = []
    monkeypatch.setattr(
        "eve_trader.actions.TokenManager.get_record",
        lambda self, role: looked.append(role) or None,
    )
    with pytest.raises(ActionError, match="Invalid role_key"):
        do_wallet_transactions("producer:1")
    with pytest.raises(ActionError, match="Invalid role_key"):
        do_wallet_balance("doctrine:1")
    assert looked == []


# --------------------------------------------------------------- esi: wallet reads
# Confirmed real bug 2026-09-21: an esi:<id> character (added via the
# Characters page) correctly appears in list_shared_trading_characters's
# sharing-based Transactions-tab picker, but do_wallet_transactions/
# do_wallet_balance still gated on TOOL_ROLE_PREFIXES["trading"]
# (buyer/seller only), so selecting it always raised "Invalid role_key" -
# no transactions ever loaded for a shared, re-authed seller.


@pg_helpers.postgres_required()
def test_wallet_transactions_esi_key_rejected_without_wallet_sharing(tenant, _apply_esi_access_schema):  # noqa: F811
    from dataclasses import asdict

    from eve_trader import storage
    from eve_trader.auth import TokenRecord

    rec = TokenRecord(
        role="esi:1001", character_id=1001, character_name="Alice",
        access_token="a", refresh_token="r", expires_at=9999999999.0,
        scopes="esi-wallet.read_character_wallet.v1",
    )
    storage.save_tenant_token("esi:1001", asdict(rec))

    with pytest.raises(ActionError, match="has not shared Wallet"):
        do_wallet_transactions("esi:1001")
    with pytest.raises(ActionError, match="has not shared Wallet"):
        do_wallet_balance("esi:1001")


@pg_helpers.postgres_required()
def test_wallet_transactions_esi_key_accepted_once_wallet_shared(tenant, _apply_esi_access_schema, monkeypatch):  # noqa: F811
    from dataclasses import asdict

    from eve_trader import storage
    from eve_trader.auth import TokenRecord

    rec = TokenRecord(
        role="esi:1001", character_id=1001, character_name="Alice",
        access_token="a", refresh_token="r", expires_at=9999999999.0,
        scopes="esi-wallet.read_character_wallet.v1",
    )
    storage.save_tenant_token("esi:1001", asdict(rec))
    storage.upsert_esi_sharing("character", 1001, "wallet", "trading")

    monkeypatch.setattr(
        "eve_trader.actions.fetch_recent_transactions",
        lambda character_id, auth_role, client, lookback_days: [],
    )
    assert do_wallet_transactions("esi:1001") == []

    monkeypatch.setattr(
        "eve_trader.esi_client.ESIClient.character_wallet_balance",
        lambda self, character_id, auth_role: 123.0,
    )
    assert do_wallet_balance("esi:1001") == {"role_key": "esi:1001", "balance": 123.0}

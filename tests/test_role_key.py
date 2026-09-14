"""F-05: canonical role_key domain."""
from __future__ import annotations

import pytest

from eve_trader.actions import ActionError, do_remove_trading_character
from eve_trader.auth import InvalidRoleKey, validate_role_key


VALID = (
    "buyer:1",
    "seller:2112625428",
    "producer:42",
    "doctrine:99",
    "doctrine-assets:99",
    "trader:123456789012345",
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

"""Tests for actions._list_role_characters (GitHub issue #46: buyer/seller
support multiple characters, same multi-key scheme "producer" already used,
plus a backward-compat fallback to the old single fixed-key token for a
character that hasn't been re-added yet)."""
from eve_trader.actions import _list_role_characters
from eve_trader.auth import TokenRecord


def _record(role: str, character_id: int, character_name: str = "Test Char") -> TokenRecord:
    return TokenRecord(
        role=role, character_id=character_id, character_name=character_name,
        access_token="access", refresh_token="refresh", expires_at=9999999999.0, scopes="",
    )


class FakeTokens:
    """Minimal TokenManager double - _list_role_characters only ever calls
    list_roles/get_record, never anything that touches storage."""
    def __init__(self, records: dict[str, TokenRecord]):
        self._records = records

    def list_roles(self, prefix: str) -> list[str]:
        return [role for role in self._records if role.startswith(f"{prefix}:")]

    def get_record(self, role: str):
        return self._records.get(role)


def test_no_tokens_returns_empty_list():
    assert _list_role_characters(FakeTokens({}), "seller") == []


def test_multi_key_characters_are_listed():
    tokens = FakeTokens({
        "seller:1": _record("seller:1", 1, "Alice"),
        "seller:2": _record("seller:2", 2, "Bob"),
        "buyer:3": _record("buyer:3", 3, "Carol"),  # different prefix - must not leak in
    })
    result = _list_role_characters(tokens, "seller")
    assert sorted(result) == [("seller:1", 1, "Alice"), ("seller:2", 2, "Bob")]


def test_legacy_single_key_included_as_fallback():
    # Real-world shape: a token logged in before issue #46 under the old
    # fixed "seller" key, never re-added since - it must still work.
    tokens = FakeTokens({"seller": _record("seller", 42, "Legacy Seller")})
    assert _list_role_characters(tokens, "seller") == [("seller", 42, "Legacy Seller")]


def test_legacy_key_not_duplicated_once_superseded_by_a_multi_key_entry():
    # Same character re-added under the new scheme - the old key must not
    # also be listed a second time for the same character_id.
    tokens = FakeTokens({
        "seller": _record("seller", 42, "Legacy Seller"),
        "seller:42": _record("seller:42", 42, "Legacy Seller"),
    })
    assert _list_role_characters(tokens, "seller") == [("seller:42", 42, "Legacy Seller")]


def test_legacy_key_still_included_alongside_a_different_characters_multi_key():
    tokens = FakeTokens({
        "seller": _record("seller", 42, "Legacy Seller"),
        "seller:99": _record("seller:99", 99, "New Seller"),
    })
    result = _list_role_characters(tokens, "seller")
    assert sorted(result) == [("seller", 42, "Legacy Seller"), ("seller:99", 99, "New Seller")]

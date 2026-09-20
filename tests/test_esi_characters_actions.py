"""Phase 5/6 Characters do_* : access preview, sharing toggle, reauth scopes."""
from __future__ import annotations

import pytest

from eve_trader.auth import TokenRecord
from eve_trader.esi_data import actions as esi_actions
from eve_trader.esi_data.actions import ActionError
from eve_trader.esi_data.registry import OWNED_DATA_KINDS

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
ASSETS_SCOPE = "esi-assets.read_assets.v1"
WALLET_SCOPE = "esi-wallet.read_character_wallet.v1"


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("esi_sharing", "esi_character_capabilities", "tenant_tokens")
    yield
    pg_helpers.wipe_tables("esi_sharing", "esi_character_capabilities", "tenant_tokens")


def _save_token(character_id, role, scopes, name="Alice"):
    rec = TokenRecord(
        role=role, character_id=character_id, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0,
        scopes=scopes,
    )
    from dataclasses import asdict
    from eve_trader import storage
    storage.save_tenant_token(role, asdict(rec))
    return rec


def test_access_preview_marks_every_item_added_when_no_character():
    assets = next(k for k in OWNED_DATA_KINDS if k.key == "assets")
    preview = esi_actions.do_access_preview(
        [assets.character_scope, assets.corporation_scope],
        title="Production — Producer Character",
    )
    assert preview["title"].startswith("Production")
    by_key = {i["key"]: i for i in preview["items"]}
    assert by_key["assets"]["added"] is True
    assert by_key["assets"]["label"] == "Assets"


def test_access_preview_highlights_only_kinds_not_on_existing_tokens(tenant):
    _save_token(ALICE, f"producer:{ALICE}", ASSETS_SCOPE)
    wallet = next(k for k in OWNED_DATA_KINDS if k.key == "wallet")
    preview = esi_actions.do_access_preview(
        [ASSETS_SCOPE, wallet.character_scope, wallet.corporation_scope],
        character_id=ALICE,
    )
    by_key = {i["key"]: i for i in preview["items"]}
    assert by_key["assets"]["added"] is False
    assert by_key["wallet"]["added"] is True


def test_reauth_scopes_unions_existing_and_extra_kinds(tenant):
    _save_token(ALICE, f"producer:{ALICE}", ASSETS_SCOPE)
    scopes = esi_actions.do_reauth_scopes(ALICE, extra_kinds=["wallet"])
    assert ASSETS_SCOPE in scopes
    assert WALLET_SCOPE in scopes


def test_set_sharing_toggles_and_rejects_non_consuming_tool(tenant):
    esi_actions.do_set_sharing("character", ALICE, "assets", "production", True)
    rows = esi_actions.do_list_sharing("production")
    assert rows == [{
        "owner_type": "character", "owner_id": ALICE,
        "data_kind": "assets", "tool_key": "production",
    }]
    esi_actions.do_set_sharing("character", ALICE, "assets", "production", False)
    assert esi_actions.do_list_sharing("production") == []
    with pytest.raises(ActionError, match="does not consume"):
        esi_actions.do_set_sharing("character", ALICE, "assets", "admin", True)
    with pytest.raises(ActionError, match="Unknown data_kind"):
        esi_actions.do_set_sharing("character", ALICE, "not_a_kind", "production", True)


def test_list_token_characters_flags_a_pool(tenant, monkeypatch):
    from eve_trader.esi_client import ESIClient
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, character_id: {"corporation_id": 9001})
    _save_token(ALICE, f"producer:{ALICE}", ASSETS_SCOPE)
    _save_token(ALICE, f"doctrine-assets:{ALICE}", ASSETS_SCOPE)
    rows = esi_actions.do_list_token_characters()
    assert len(rows) == 1
    assert rows[0]["character_id"] == ALICE
    assert rows[0]["character_has_token_pool"] is True
    assert rows[0]["write_role"] == f"doctrine-assets:{ALICE}"


# --------------------------------------------------------------- Known gap 2


def test_list_token_characters_includes_corporation_id(tenant, monkeypatch):
    from eve_trader.esi_client import ESIClient
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, character_id: {"corporation_id": 9001})
    _save_token(ALICE, f"esi:{ALICE}", ASSETS_SCOPE)

    rows = esi_actions.do_list_token_characters()

    assert rows[0]["corporation_id"] == 9001


def test_list_token_characters_corporation_id_is_none_on_lookup_failure(tenant, monkeypatch):
    from eve_trader.esi_client import ESIClient

    def _boom(self, character_id):
        raise Exception("ESI unreachable")  # noqa: BLE001 - simulating an arbitrary live-ESI failure
    monkeypatch.setattr(ESIClient, "character_public_info", _boom)
    _save_token(ALICE, f"esi:{ALICE}", ASSETS_SCOPE)

    rows = esi_actions.do_list_token_characters()

    assert rows[0]["corporation_id"] is None


BOB = 1002
CORP = 9001


def _tick_roles_capability(character_id):
    from eve_trader import storage
    storage.upsert_esi_character_capability(character_id, "corporation_roles")


def test_check_corporation_roles_reports_true_when_a_checked_member_holds_it(tenant, monkeypatch):
    from eve_trader.esi_client import ESIClient
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, character_id: {"corporation_id": CORP})
    monkeypatch.setattr(ESIClient, "character_roles", lambda self, character_id, auth_role: {"roles": ["Director"]})
    _save_token(ALICE, f"esi:{ALICE}", "esi-characters.read_corporation_roles.v1")
    _tick_roles_capability(ALICE)

    result = esi_actions.do_check_corporation_roles()

    corp = result["corporations"][0]
    assert corp["corporation_id"] == CORP
    assert corp["checked_characters"] == ["Alice"]
    assert corp["data_kinds"]["assets"] == {"required_roles": ["Director"], "has_role": True}
    assert corp["data_kinds"]["market_orders"] == {
        "required_roles": ["Accountant", "Trader"], "has_role": False,
    }


def test_check_corporation_roles_reports_none_when_nobody_is_checked(tenant, monkeypatch):
    from eve_trader.esi_client import ESIClient
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, character_id: {"corporation_id": CORP})
    _save_token(ALICE, f"esi:{ALICE}", ASSETS_SCOPE)
    # Capability never ticked for Alice.

    result = esi_actions.do_check_corporation_roles()

    corp = result["corporations"][0]
    assert corp["checked_characters"] == []
    assert corp["unchecked_characters"] == ["Alice"]
    assert corp["data_kinds"]["assets"] == {"required_roles": ["Director"], "has_role": None}


def test_check_corporation_roles_skips_ticked_character_whose_token_lacks_the_scope(tenant, monkeypatch):
    from eve_trader.esi_client import ESIClient
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, character_id: {"corporation_id": CORP})
    monkeypatch.setattr(ESIClient, "character_roles", lambda self, character_id, auth_role:
                         pytest.fail("must not call character_roles with no scope-holding token"))
    _save_token(ALICE, f"esi:{ALICE}", ASSETS_SCOPE)  # ticked, but no corporation_roles scope yet
    _tick_roles_capability(ALICE)

    result = esi_actions.do_check_corporation_roles()

    corp = result["corporations"][0]
    assert corp["unchecked_characters"] == ["Alice"]
    assert corp["data_kinds"]["assets"]["has_role"] is None


def test_check_corporation_roles_second_corp_member_covers_a_missing_role(tenant, monkeypatch):
    """Alice (Director) and Bob (Accountant) in the same corp - Assets is
    covered by Alice, Market Orders by Bob, each corp-wide, not per
    character."""
    from eve_trader.esi_client import ESIClient
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, character_id: {"corporation_id": CORP})

    def _roles(self, character_id, auth_role):
        return {"roles": ["Director"] if character_id == ALICE else ["Accountant"]}
    monkeypatch.setattr(ESIClient, "character_roles", _roles)
    _save_token(ALICE, f"esi:{ALICE}", "esi-characters.read_corporation_roles.v1")
    _tick_roles_capability(ALICE)
    _save_token(BOB, f"esi:{BOB}", "esi-characters.read_corporation_roles.v1", name="Bob")
    _tick_roles_capability(BOB)

    result = esi_actions.do_check_corporation_roles()

    corp = result["corporations"][0]
    assert corp["checked_characters"] == ["Alice", "Bob"]
    assert corp["data_kinds"]["assets"]["has_role"] is True
    assert corp["data_kinds"]["market_orders"]["has_role"] is True
    # wallet's corp_roles is (Accountant, Junior_Accountant) - Bob's
    # Accountant role covers this too, not just market_orders.
    assert corp["data_kinds"]["wallet"]["has_role"] is True

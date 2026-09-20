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


def test_list_token_characters_flags_a_pool(tenant):
    _save_token(ALICE, f"producer:{ALICE}", ASSETS_SCOPE)
    _save_token(ALICE, f"doctrine-assets:{ALICE}", ASSETS_SCOPE)
    rows = esi_actions.do_list_token_characters()
    assert len(rows) == 1
    assert rows[0]["character_id"] == ALICE
    assert rows[0]["character_has_token_pool"] is True
    assert rows[0]["write_role"] == f"doctrine-assets:{ALICE}"

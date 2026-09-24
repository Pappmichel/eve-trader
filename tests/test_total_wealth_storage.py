"""storage.py's Total Wealth read helpers (PORTFOLIO_REWORK_PLAN.md
section 6): load_all_assets and sum_wallet_balances. load_owned_blueprints
already has its own coverage elsewhere; not duplicated here."""
import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

TYPE_ID = 34
CHAR_ID = 1001
CORP_ID = 98000001


@pytest.fixture(autouse=True)
def _wipe():
    # character_assets/corp_assets are the column-only-bucket exception
    # (PK is (item_id, owner_name), not tenant-scoped) - a fresh `tenant`
    # fixture alone does not stop a hardcoded item_id from colliding with
    # another test's row under a different tenant (CLAUDE.md's own
    # testing-conventions note).
    pg_helpers.wipe_tables("character_assets", "corp_assets")
    yield


def test_load_all_assets_excludes_blueprint_items(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, 60003760, "Hangar", 100, 0, "Alice"),
        (2, 11567, 60003760, "Hangar", 1, 1, "Alice"),  # is_blueprint_copy - excluded
    ], owner_character_id=CHAR_ID)

    rows = storage.load_all_assets([CHAR_ID], [])
    assert rows == [(TYPE_ID, 100)]


def test_load_all_assets_combines_character_and_corp(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, 60003760, "Hangar", 100, 0, "Alice"),
    ], owner_character_id=CHAR_ID)
    storage.replace_assets("corp_assets", [
        (2, TYPE_ID, 60003760, "Hangar", 50, 0, "Test Corp"),
    ], owner_corporation_id=CORP_ID)

    rows = sorted(storage.load_all_assets([CHAR_ID], [CORP_ID]))
    assert rows == [(TYPE_ID, 50), (TYPE_ID, 100)]


def test_load_all_assets_empty_owner_list_excludes_everyone(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, 60003760, "Hangar", 100, 0, "Alice"),
    ], owner_character_id=CHAR_ID)

    assert storage.load_all_assets([], []) == []


def test_sum_wallet_balances_character_and_corp(tenant):
    storage.upsert_character_wallet_balance(CHAR_ID, 1000.0)
    storage.replace_corp_wallet_balances(CORP_ID, {1: 500.0, 2: 250.0})

    assert storage.sum_wallet_balances([CHAR_ID], [CORP_ID]) == 1750.0


def test_sum_wallet_balances_empty_lists_is_zero(tenant):
    storage.upsert_character_wallet_balance(CHAR_ID, 1000.0)
    assert storage.sum_wallet_balances([], []) == 0.0


def test_sum_wallet_balances_none_lists_is_zero(tenant):
    assert storage.sum_wallet_balances() == 0.0

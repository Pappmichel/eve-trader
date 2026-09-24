"""storage.py's manual_item_prices functions (PORTFOLIO_REWORK_PLAN.md
section 7) - real Postgres, plain CRUD with no business logic worth
mocking."""
import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_portfolio_schema, tenant, tenant_pair  # noqa: F401

pytestmark = pg_helpers.postgres_required()


def test_upsert_and_load_manual_item_price(tenant):
    storage.upsert_manual_item_price(34, "Tritanium", 5.5)
    assert storage.load_manual_item_prices() == {34: 5.5}


def test_upsert_overwrites_existing_price(tenant):
    storage.upsert_manual_item_price(34, "Tritanium", 5.5)
    storage.upsert_manual_item_price(34, "Tritanium", 6.0)
    assert storage.load_manual_item_prices() == {34: 6.0}
    rows = storage.list_manual_item_prices()
    assert len(rows) == 1
    assert rows[0][2] == 6.0


def test_list_manual_item_prices_name_ordered_with_isoformat_timestamp(tenant):
    storage.upsert_manual_item_price(35, "Pyerite", 10.0)
    storage.upsert_manual_item_price(34, "Tritanium", 5.5)
    rows = storage.list_manual_item_prices()
    assert [r[1] for r in rows] == ["Pyerite", "Tritanium"]
    for row in rows:
        assert isinstance(row[3], str)


def test_delete_manual_item_price(tenant):
    storage.upsert_manual_item_price(34, "Tritanium", 5.5)
    storage.delete_manual_item_price(34)
    assert storage.load_manual_item_prices() == {}


def test_delete_nonexistent_price_is_noop(tenant):
    storage.delete_manual_item_price(999)
    assert storage.load_manual_item_prices() == {}


def test_manual_item_prices_isolated_between_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.upsert_manual_item_price(34, "Tritanium", 5.5)
    with storage.tenant_context(tenant_b):
        assert storage.load_manual_item_prices() == {}

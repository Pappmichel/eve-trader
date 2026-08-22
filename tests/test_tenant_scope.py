"""Tests for tenant_scope.enter_tenant - proves it resolves/resets storage's
tenant contextvar and both TRADING_CONFIG/PRODUCTION_CONFIG's per-tenant
instance together (multi-tenant migration Phase 4) - see
docs/MULTI_TENANT_PLAN.md.
"""
from eve_trader import storage, tenant_scope
from eve_trader.config import TRADING_CONFIG
from eve_trader.production.config import PRODUCTION_CONFIG
from eve_trader.refining.config import REFINING_CONFIG

from pathlib import Path

import pytest

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, tenant, tenant_pair  # noqa: F401

_REFINING_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "refining_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_refining_schema(_apply_phase1_schema, _apply_phase2_schema):
    """Widens tenant_settings' scope CHECK to allow 'refining' - see
    test_storage_refining.py's own copy of this fixture for the full
    reasoning. Needed here too since test_enter_tenant_resolves_saved_
    refining_overrides below saves a real "refining"-scope row."""
    if not pg_helpers._postgres_available():
        return
    with pg_helpers.psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_REFINING_SCHEMA_SQL.read_text(encoding="utf-8"))


@pg_helpers.postgres_required()
def test_enter_tenant_resolves_saved_trading_overrides(tenant):
    storage.save_tenant_settings("trading", {"min_hit_rate": 0.42})

    with tenant_scope.enter_tenant(tenant):
        assert TRADING_CONFIG.min_hit_rate == 0.42


@pg_helpers.postgres_required()
def test_enter_tenant_resolves_saved_production_overrides(tenant):
    storage.save_tenant_settings("production", {"min_margin": 0.42})

    with tenant_scope.enter_tenant(tenant):
        assert PRODUCTION_CONFIG.min_margin == 0.42


@pg_helpers.postgres_required()
def test_enter_tenant_sets_storages_own_ambient_tenant(tenant):
    with tenant_scope.enter_tenant(tenant):
        assert storage.get_current_tenant() == tenant


@pg_helpers.postgres_required()
def test_enter_tenant_resets_everything_on_exit(_apply_phase1_schema, _apply_phase2_schema):
    import uuid
    tenant_id = str(uuid.uuid4())
    with storage.tenant_context(tenant_id):
        storage.save_tenant_settings("trading", {"min_hit_rate": 0.42})
    default_min_hit_rate = TRADING_CONFIG.min_hit_rate

    with tenant_scope.enter_tenant(tenant_id):
        pass

    assert storage.get_current_tenant() is None
    assert TRADING_CONFIG.min_hit_rate == default_min_hit_rate


@pg_helpers.postgres_required()
def test_enter_tenant_isolates_two_tenants_configs(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.save_tenant_settings("trading", {"min_hit_rate": 0.11})
    with storage.tenant_context(tenant_b):
        storage.save_tenant_settings("trading", {"min_hit_rate": 0.99})

    with tenant_scope.enter_tenant(tenant_a):
        assert TRADING_CONFIG.min_hit_rate == 0.11

    with tenant_scope.enter_tenant(tenant_b):
        assert TRADING_CONFIG.min_hit_rate == 0.99


@pg_helpers.postgres_required()
def test_enter_tenant_resolves_saved_refining_overrides(tenant):
    storage.save_tenant_settings("refining", {"reprocessing_skill_level": 5})

    with tenant_scope.enter_tenant(tenant):
        assert REFINING_CONFIG.reprocessing_skill_level == 5

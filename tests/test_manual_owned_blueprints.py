"""Tests for storage.py's manual_owned_blueprints CRUD/engine-helper
functions (docs/MANUAL_TRACKING_PLAN.md phase 5), Postgres-backed."""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

BP_TYPE_ID = 690  # Rifter Blueprint (real type_id not required for these tests)
LOCATION_A = 1000000000001
LOCATION_B = 1000000000002


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("sde_types", "sde_blueprint_products")
    yield


def _insert_sde_type(type_id: int, name: str) -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_types (type_id, type_name, published) VALUES (?, ?, 1) "
            "ON CONFLICT (type_id) DO UPDATE SET type_name = excluded.type_name",
            (type_id, name),
        )


def test_insert_then_get_manual_owned_blueprint(tenant):
    with storage.tenant_context(tenant):
        manual_id = storage.insert_manual_owned_blueprint(BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A)

        row = storage.get_manual_owned_blueprint(manual_id)

    assert row == (manual_id, BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A)


def test_get_manual_owned_blueprint_missing_id_returns_none(tenant):
    with storage.tenant_context(tenant):
        assert storage.get_manual_owned_blueprint(999999) is None


def test_update_manual_owned_blueprint_changes_mutable_fields(tenant):
    with storage.tenant_context(tenant):
        manual_id = storage.insert_manual_owned_blueprint(BP_TYPE_ID, False, 4, 8, 5, 1, LOCATION_A)

        storage.update_manual_owned_blueprint(manual_id, 6, 12, 10, 3)

        row = storage.get_manual_owned_blueprint(manual_id)
    assert row == (manual_id, BP_TYPE_ID, False, 6, 12, 10, 3, LOCATION_A)


def test_delete_manual_owned_blueprint(tenant):
    with storage.tenant_context(tenant):
        manual_id = storage.insert_manual_owned_blueprint(BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A)
        storage.delete_manual_owned_blueprint(manual_id)

        assert storage.get_manual_owned_blueprint(manual_id) is None


def test_load_manual_owned_blueprints_includes_type_name(tenant):
    _insert_sde_type(BP_TYPE_ID, "Rifter Blueprint")
    with storage.tenant_context(tenant):
        manual_id = storage.insert_manual_owned_blueprint(BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A)

        rows = storage.load_manual_owned_blueprints()

    assert rows == [(manual_id, BP_TYPE_ID, "Rifter Blueprint", True, 10, 20, None, 1, LOCATION_A)]


def test_manual_bpo_best_me_te_is_max_across_originals(tenant):
    with storage.tenant_context(tenant):
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, True, 4, 20, None, 1, LOCATION_A)
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, True, 10, 8, None, 1, LOCATION_B)
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, False, 10, 20, 5, 1, LOCATION_A)  # a BPC - ignored

        assert storage.manual_bpo_best_me_te(BP_TYPE_ID) == (10, 20)


def test_manual_bpo_best_me_te_none_when_no_bpo(tenant):
    with storage.tenant_context(tenant):
        assert storage.manual_bpo_best_me_te(BP_TYPE_ID) is None


def test_manual_bpc_runs_sums_runs_times_quantity(tenant):
    with storage.tenant_context(tenant):
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, False, 4, 8, 5, 3, LOCATION_A)  # 15 runs
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, False, 4, 8, 2, 1, LOCATION_A)  # 2 runs
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A)  # BPO - ignored

        assert storage.manual_bpc_runs(BP_TYPE_ID, LOCATION_A) == 17
        assert storage.manual_bpc_runs(BP_TYPE_ID, None) == 17


def test_manual_bpc_runs_filters_by_location(tenant):
    with storage.tenant_context(tenant):
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, False, 4, 8, 5, 1, LOCATION_A)
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, False, 4, 8, 3, 1, LOCATION_B)

        assert storage.manual_bpc_runs(BP_TYPE_ID, LOCATION_A) == 5
        assert storage.manual_bpc_runs(BP_TYPE_ID, LOCATION_B) == 3
        assert storage.manual_bpc_runs(BP_TYPE_ID, None) == 8


def test_manual_has_bpo_at_location(tenant):
    with storage.tenant_context(tenant):
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A)

        assert storage.manual_has_bpo_at_location(BP_TYPE_ID, LOCATION_A) is True
        assert storage.manual_has_bpo_at_location(BP_TYPE_ID, LOCATION_B) is False


def test_manual_has_bpo_at_location_false_for_bpc_only(tenant):
    with storage.tenant_context(tenant):
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, False, 4, 8, 5, 1, LOCATION_A)

        assert storage.manual_has_bpo_at_location(BP_TYPE_ID, LOCATION_A) is False


def test_is_known_blueprint(tenant):
    with storage.tenant_context(tenant), storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_blueprint_products (blueprint_type_id, activity_id, product_type_id, quantity) "
            "VALUES (?, 1, 34, 1)",
            (BP_TYPE_ID,),
        )
    with storage.tenant_context(tenant):
        assert storage.is_known_blueprint(BP_TYPE_ID) is True
        assert storage.is_known_blueprint(999999) is False


def test_manual_owned_blueprints_are_tenant_isolated(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.insert_manual_owned_blueprint(BP_TYPE_ID, True, 10, 20, None, 1, LOCATION_A)
    with storage.tenant_context(tenant_b):
        assert storage.load_manual_owned_blueprints() == []

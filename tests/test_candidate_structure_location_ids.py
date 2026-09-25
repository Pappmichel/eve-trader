"""storage.candidate_structure_location_ids - docs/MANUAL_TRACKING_PLAN.md
phase 8. Collects every distinct location_id referenced across the tables a
structure-name resolution candidate could come from, for the current
tenant. Pg-gated - needs real RLS-scoped tables, not a monkeypatch."""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def test_empty_when_nothing_references_any_location(tenant):
    assert storage.candidate_structure_location_ids() == set()


def test_collects_from_character_and_corp_assets(tenant):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_assets (item_id, type_id, location_id, owner_name) VALUES (%s, %s, %s, %s)",
            (1, 100, 1035466617946, "Alice"),
        )
        conn.execute(
            "INSERT INTO corp_assets (item_id, type_id, location_id, owner_name) VALUES (%s, %s, %s, %s)",
            (2, 100, 1035466617947, "Alice Corp"),
        )

    assert storage.candidate_structure_location_ids() == {1035466617946, 1035466617947}


def test_collects_from_character_and_corp_blueprints(tenant):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_blueprints (item_id, type_id, location_id) VALUES (%s, %s, %s)",
            (1, 100, 1035466617946),
        )
        conn.execute(
            "INSERT INTO corp_blueprints (item_id, type_id, location_id) VALUES (%s, %s, %s)",
            (2, 100, 1035466617947),
        )

    assert storage.candidate_structure_location_ids() == {1035466617946, 1035466617947}


def test_collects_from_industry_jobs_output_location(tenant):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_industry_jobs (job_id, product_type_id, output_location_id) VALUES (%s, %s, %s)",
            (1, 100, 1035466617946),
        )
        conn.execute(
            "INSERT INTO corp_industry_jobs (job_id, product_type_id, output_location_id) VALUES (%s, %s, %s)",
            (2, 100, 1035466617947),
        )

    assert storage.candidate_structure_location_ids() == {1035466617946, 1035466617947}


def test_collects_from_job_category_and_category_location_options(tenant):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO job_category_locations (category, location_id) VALUES (%s, %s)",
            ("manufacturing", 1035466617946),
        )
        conn.execute(
            "INSERT INTO category_location_options (category, location_id) VALUES (%s, %s)",
            ("manufacturing", 1035466617947),
        )

    assert storage.candidate_structure_location_ids() == {1035466617946, 1035466617947}


def test_collects_from_manual_stock_and_manual_owned_blueprints(tenant):
    storage.upsert_manual_stock(100, 5, location_id=1035466617946)
    storage.insert_manual_owned_blueprint(
        blueprint_type_id=200, is_original=True, material_efficiency=10, time_efficiency=20,
        runs=None, quantity=1, location_id=1035466617947,
    )

    assert storage.candidate_structure_location_ids() == {1035466617946, 1035466617947}


def test_deduplicates_across_tables(tenant):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_assets (item_id, type_id, location_id, owner_name) VALUES (%s, %s, %s, %s)",
            (1, 100, 1035466617946, "Alice"),
        )
        conn.execute(
            "INSERT INTO character_blueprints (item_id, type_id, location_id) VALUES (%s, %s, %s)",
            (2, 100, 1035466617946),
        )

    assert storage.candidate_structure_location_ids() == {1035466617946}


def test_does_not_leak_across_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        with storage.connect() as conn:
            conn.execute(
                "INSERT INTO character_assets (item_id, type_id, location_id, owner_name) VALUES (%s, %s, %s, %s)",
                (1, 100, 1035466617946, "Alice"),
            )

    with storage.tenant_context(tenant_b):
        assert storage.candidate_structure_location_ids() == set()

    with storage.tenant_context(tenant_a):
        assert storage.candidate_structure_location_ids() == {1035466617946}

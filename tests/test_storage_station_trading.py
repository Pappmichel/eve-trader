"""Tests for storage.py's station_trading_shortlist activate/deactivate
functions - T3-08 (business-logic audit follow-up, 2026-09-26): these had
zero test coverage at any layer (only a router-level test mocking the
action existed for deactivate; activate had none at all)."""
from pathlib import Path

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_STATION_TRADING_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "station_trading_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_station_trading_schema(_apply_phase1_schema):
    """docs/station_trading_schema.sql, applied once per session via the
    owner role - same pattern as test_storage_refining.py's own
    _apply_refining_schema."""
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_STATION_TRADING_SCHEMA_SQL.read_text(encoding="utf-8"))


def _insert_row(type_id: int, active: bool = True) -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO station_trading_shortlist (type_id, spread_pct, avg_daily_volume, discovered_at, active) "
            "VALUES (?, ?, ?, ?, ?)",
            (type_id, 0.1, 1000.0, "2026-09-26T00:00:00", active),
        )


def _active_flags(tenant_id) -> dict:
    with storage.tenant_context(tenant_id), storage.connect() as conn:
        rows = conn.execute("SELECT type_id, active FROM station_trading_shortlist").fetchall()
    return {type_id: active for type_id, active in rows}


def test_deactivate_sets_active_false_for_the_given_type_ids_only(tenant):
    _insert_row(34)
    _insert_row(35)

    storage.deactivate_station_trading_shortlist_items([34])

    assert _active_flags(tenant) == {34: False, 35: True}


def test_activate_reverses_a_deactivation(tenant):
    # Real-world case this exists for: an item deactivated once, then
    # re-discovered by a later Refresh Shortlist - must be reactivatable,
    # not stuck inactive forever (upsert never touches `active` on its own).
    _insert_row(34, active=False)

    storage.activate_station_trading_shortlist_items([34])

    assert _active_flags(tenant) == {34: True}


def test_deactivate_empty_list_is_a_no_op(tenant):
    _insert_row(34)

    storage.deactivate_station_trading_shortlist_items([])

    assert _active_flags(tenant) == {34: True}


def test_activate_empty_list_is_a_no_op(tenant):
    _insert_row(34, active=False)

    storage.activate_station_trading_shortlist_items([])

    assert _active_flags(tenant) == {34: False}


def test_activate_and_deactivate_are_tenant_scoped(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        _insert_row(34)
    with storage.tenant_context(tenant_b):
        _insert_row(34)

    with storage.tenant_context(tenant_a):
        storage.deactivate_station_trading_shortlist_items([34])

    assert _active_flags(tenant_a) == {34: False}
    assert _active_flags(tenant_b) == {34: True}  # unaffected - RLS scopes the UPDATE

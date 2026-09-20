"""Phase 1 isolation tests for esi_sharing / esi_freshness /
esi_character_capabilities / esi_wallet_transactions /
esi_wallet_journal (docs/ESI_ACCESS_PLAN.md). Modelled on
test_pg_tenant_isolation.py: real storage.connect() + RLS, not a raw
SQL stand-in. Group-3 capabilities have no tool_key column.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_sorting import _apply_sorting_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_TABLES = (
    "esi_sharing",
    "esi_freshness",
    "esi_character_capabilities",
    "esi_wallet_transactions",
    "esi_wallet_journal",
)

_OWNER_ID_TABLES = (
    "character_assets",
    "corp_assets",
    "character_industry_jobs",
    "corp_industry_jobs",
    "character_blueprints",
    "corp_blueprints",
    "character_sell_orders",
    "character_slots",
    "esi_wallet_transactions",
    "esi_wallet_journal",
)

_WALLET_TXN_SQL = (
    "INSERT INTO esi_wallet_transactions ("
    "owner_type, owner_id, division, transaction_id, date, type_id, "
    "location_id, unit_price, quantity, is_buy, journal_ref_id, "
    "owner_character_id, owner_corporation_id"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_WALLET_JOURNAL_SQL = (
    "INSERT INTO esi_wallet_journal ("
    "owner_type, owner_id, division, journal_id, date, ref_type, amount, "
    "owner_character_id, owner_corporation_id"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


@pytest.fixture(autouse=True)
def _clean(tenant_pair, _apply_esi_access_schema):
    pg_helpers.clean_tables(tenant_pair, *_TABLES)
    yield
    pg_helpers.clean_tables(tenant_pair, *_TABLES)


def test_two_tenants_can_share_the_same_owner_kind_tool_without_colliding(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    sql = (
        "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
        "VALUES ('character', ?, 'assets', 'production')"
    )
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(sql, (123,))
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute(sql, (123,))


def test_a_tenant_only_sees_its_own_sharing_rows(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, 'assets', 'production')",
            (123,),
        )
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, 'assets', 'doctrine')",
            (123,),
        )
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        rows = conn.execute("SELECT tool_key FROM esi_sharing").fetchall()
    assert rows == [("production",)]
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        rows = conn.execute("SELECT tool_key FROM esi_sharing").fetchall()
    assert rows == [("doctrine",)]


def test_sharing_conflict_is_tenant_scoped(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    upsert = (
        "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
        "VALUES ('character', ?, 'assets', 'production') ON CONFLICT DO NOTHING"
    )
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(upsert, (123,))
        conn.execute(upsert, (123,))
        count = conn.execute("SELECT count(*) FROM esi_sharing").fetchone()[0]
    assert count == 1
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute(upsert, (123,))
        count = conn.execute("SELECT count(*) FROM esi_sharing").fetchone()[0]
    assert count == 1


def test_freshness_two_tenants_same_owner_kind(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    sql = (
        "INSERT INTO esi_freshness (owner_type, owner_id, data_kind, last_error) "
        "VALUES ('character', ?, 'wallet', ?)"
    )
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(sql, (9, "err-a"))
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute(sql, (9, "err-b"))
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        assert conn.execute("SELECT last_error FROM esi_freshness").fetchall() == [("err-a",)]


def test_capabilities_have_no_tool_key_column(_apply_esi_access_schema):
    with psycopg.connect(pg_helpers.OWNER_DSN) as conn:
        cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'esi_character_capabilities'"
            ).fetchall()
        }
    assert "tool_key" not in cols
    assert "character_id" in cols
    assert "capability_key" in cols


def test_capabilities_isolated_across_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    sql = (
        "INSERT INTO esi_character_capabilities (character_id, capability_key) "
        "VALUES (?, 'structure_market_book')"
    )
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(sql, (11,))
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute(sql, (11,))
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        assert conn.execute("SELECT count(*) FROM esi_character_capabilities").fetchone()[0] == 1


def test_wallet_transactions_isolated_and_namespaced(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    when = datetime(2026, 9, 20, tzinfo=timezone.utc)
    char_row = (
        "character", 42, 0, 1001, when, 34, 60003760, 1.5, 10, True, 5001, 42, None,
    )
    corp_row = (
        "corporation", 99, 1, 1001, when, 34, 60003760, 2.0, 5, False, 5002, None, 99,
    )
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(_WALLET_TXN_SQL, char_row)
        conn.execute(_WALLET_TXN_SQL, corp_row)
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute(_WALLET_TXN_SQL, char_row)
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        rows = conn.execute(
            "SELECT owner_type, owner_id, division, transaction_id "
            "FROM esi_wallet_transactions ORDER BY owner_type, division"
        ).fetchall()
    assert rows == [("character", 42, 0, 1001), ("corporation", 99, 1, 1001)]
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        rows = conn.execute(
            "SELECT owner_type FROM esi_wallet_transactions"
        ).fetchall()
    assert rows == [("character",)]


def test_wallet_journal_isolated_across_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    when = datetime(2026, 9, 20, tzinfo=timezone.utc)
    row = ("character", 42, 0, 5001, when, "market_transaction", -12.5, 42, None)
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(_WALLET_JOURNAL_SQL, row)
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute(_WALLET_JOURNAL_SQL, row)
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        assert conn.execute("SELECT count(*) FROM esi_wallet_journal").fetchone()[0] == 1


def test_character_wallet_rejects_nonzero_division(tenant_pair):
    tenant_a, _tenant_b = tenant_pair
    when = datetime(2026, 9, 20, tzinfo=timezone.utc)
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(
                _WALLET_TXN_SQL,
                ("character", 42, 1, 1001, when, 34, 60003760, 1.0, 1, True, 1, 42, None),
            )


def test_wallet_owner_ids_must_agree_with_pk(tenant_pair):
    tenant_a, _tenant_b = tenant_pair
    when = datetime(2026, 9, 20, tzinfo=timezone.utc)
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(
                _WALLET_TXN_SQL,
                ("character", 42, 0, 1001, when, 34, 60003760, 1.0, 1, True, 1, 456, None),
            )
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(
                _WALLET_JOURNAL_SQL,
                ("corporation", 99, 1, 5001, when, "market_transaction", -1.0, 42, 99),
            )
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(
            _WALLET_TXN_SQL,
            ("character", 42, 0, 1001, when, 34, 60003760, 1.0, 1, True, 1, 42, None),
        )


def test_owner_id_columns_are_bigint(
    _apply_doctrine_schema, _apply_sorting_schema, _apply_esi_access_schema,
):
    extra = (
        "doctrine_character_assets",
        "doctrine_corp_assets",
        "doctrine_contracts",
        "sorting_intake_sources",
    )
    names = list(_OWNER_ID_TABLES) + [
        "esi_sharing", "esi_freshness", "esi_character_capabilities", *extra,
    ]
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(pg_helpers._ESI_ACCESS_SCHEMA_SQL.read_text())
        placeholders = ",".join(["%s"] * len(names))
        rows = conn.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE column_name IN ('owner_character_id', 'owner_corporation_id', "
            "'owner_id', 'character_id', 'transaction_id', 'journal_id', 'journal_ref_id') "
            f"AND table_name IN ({placeholders})",
            names,
        ).fetchall()
    by_table: dict[str, dict[str, str]] = {}
    for table, column, data_type in rows:
        by_table.setdefault(table, {})[column] = data_type
    for table in _OWNER_ID_TABLES:
        cols = by_table[table]
        assert cols["owner_character_id"] == "bigint", table
        assert cols["owner_corporation_id"] == "bigint", table
    assert by_table["esi_sharing"]["owner_id"] == "bigint"
    assert by_table["esi_freshness"]["owner_id"] == "bigint"
    assert by_table["esi_character_capabilities"]["character_id"] == "bigint"
    assert by_table["esi_wallet_transactions"]["transaction_id"] == "bigint"
    assert by_table["esi_wallet_transactions"]["journal_ref_id"] == "bigint"
    assert by_table["esi_wallet_journal"]["journal_id"] == "bigint"
    for table in extra:
        cols = by_table[table]
        assert cols["owner_character_id"] == "bigint", table
        assert cols["owner_corporation_id"] == "bigint", table


def test_schema_applies_idempotently(_apply_esi_access_schema):
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(pg_helpers._ESI_ACCESS_SCHEMA_SQL.read_text())
        conn.execute(pg_helpers._ESI_ACCESS_SCHEMA_SQL.read_text())

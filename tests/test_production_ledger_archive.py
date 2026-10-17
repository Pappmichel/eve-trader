"""Production realized-profit Phase 1 archive tests.

Covers: upsert idempotence, no deletion on resync, RLS, share revoke hides
rows (D11), gap detection, retention exception (D10).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eve_trader import storage, tenant_eligibility
from eve_trader.config import ACCESS_CONFIG
from eve_trader.production.realized import access as ledger_access
from eve_trader.production.realized import db as ledger_db
from eve_trader.production.realized import ingest as ledger_ingest

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
    tenant_pair,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_LEDGER_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "production_ledger_schema.sql"

ALICE = 900001
BOB = 900002
TYPE_ID = 34
LOCATION_ID = 60003760  # Jita 4-4 NPC station when SDE present; region may be NULL in empty SDE
DATE1 = "2026-10-01T12:00:00+00:00"
DATE2 = "2026-10-10T12:00:00+00:00"


@pytest.fixture(scope="session")
def _apply_production_ledger_schema(_apply_phase1_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_LEDGER_SCHEMA_SQL.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _wipe(_apply_production_ledger_schema):
    pg_helpers.wipe_tables(
        "ledger_wallet_transactions", "ledger_wallet_journal",
        "ledger_industry_jobs", "ledger_job_inputs", "ledger_blueprints",
        "ledger_market_orders", "ledger_contracts", "ledger_contract_items",
        "ledger_archive_gaps", "esi_sharing",
    )
    yield


def _share(owner_id: int, data_kind: str, tool_key: str = "production"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
            (owner_id, data_kind, tool_key),
        )


def _revoke(owner_id: int, data_kind: str, tool_key: str = "production"):
    with storage.connect() as conn:
        conn.execute(
            "DELETE FROM esi_sharing WHERE owner_type = 'character' AND owner_id = ? "
            "AND data_kind = ? AND tool_key = ?",
            (owner_id, data_kind, tool_key),
        )


def test_wallet_upsert_is_idempotent_and_never_deletes(tenant):
    rows = [(
        "character", ALICE, 0, 1001, DATE1, TYPE_ID, LOCATION_ID,
        10.0, 5, True, 2001, None, None, None,
    )]
    ledger_db.upsert_ledger_wallet_transactions(rows)
    assert ledger_db.count_ledger_rows("ledger_wallet_transactions", "character", ALICE) == 1
    # Resync with a different freight must NOT overwrite (frozen at first sight).
    rows2 = [(
        "character", ALICE, 0, 1001, DATE1, TYPE_ID, LOCATION_ID,
        10.0, 5, True, 2001, 10000002, 300.0, 1.5,
    )]
    ledger_db.upsert_ledger_wallet_transactions(rows2)
    stored = ledger_db.list_ledger_wallet_transactions("character", ALICE)
    assert len(stored) == 1
    assert stored[0][12] is None  # freight_rate stays frozen (NULL from first insert)
    # New id appends; old stays.
    rows3 = [(
        "character", ALICE, 0, 1002, DATE2, TYPE_ID, LOCATION_ID,
        11.0, 1, False, 2002, None, None, None,
    )]
    ledger_db.upsert_ledger_wallet_transactions(rows3)
    assert ledger_db.count_ledger_rows("ledger_wallet_transactions", "character", ALICE) == 2


def test_industry_job_inputs_frozen_on_first_sight_only(tenant, monkeypatch):
    monkeypatch.setattr(ledger_ingest.inputs, "freeze_job_inputs", lambda **kw: [
        ledger_ingest.inputs.FrozenInput(
            type_id=34, quantity=10.0, blueprint_me=10, material_multiplier=0.9,
            decryptor_type_id=None, encryption_skill_level=None,
            science_skill_1_level=None, science_skill_2_level=None,
            source="assumed_me",
        )
    ])
    job = {
        "job_id": 55, "activity_id": 1, "blueprint_id": 1, "blueprint_type_id": 999,
        "product_type_id": 34, "runs": 1, "licensed_runs": None, "successful_runs": None,
        "cost": 100.0, "facility_id": None, "station_id": None, "output_location_id": None,
        "installer_id": ALICE, "start_date": DATE1, "end_date": DATE2,
        "completed_date": DATE2, "status": "delivered", "probability": None,
    }
    ledger_ingest.archive_industry_jobs(owner_type="character", owner_id=ALICE, jobs=[job])
    assert ledger_db.count_ledger_rows("ledger_industry_jobs", "character", ALICE) == 1
    assert ledger_db.count_ledger_rows("ledger_job_inputs", "character", ALICE) == 1
    first_inputs = ledger_db.list_ledger_job_inputs("character", ALICE, 55)
    assert first_inputs[0][1] == 10.0

    # Resync with a spy that would return different inputs - must not rewrite.
    monkeypatch.setattr(ledger_ingest.inputs, "freeze_job_inputs", lambda **kw: [
        ledger_ingest.inputs.FrozenInput(
            type_id=34, quantity=999.0, blueprint_me=0, material_multiplier=1.0,
            decryptor_type_id=None, encryption_skill_level=None,
            science_skill_1_level=None, science_skill_2_level=None,
            source="assumed_me",
        )
    ])
    job2 = {**job, "status": "delivered", "cost": 200.0}
    ledger_ingest.archive_industry_jobs(owner_type="character", owner_id=ALICE, jobs=[job2])
    assert ledger_db.count_ledger_rows("ledger_job_inputs", "character", ALICE) == 1
    assert ledger_db.list_ledger_job_inputs("character", ALICE, 55)[0][1] == 10.0
    # Status/cost on the job row itself may update.
    jobs = ledger_db.list_ledger_industry_jobs("character", ALICE)
    assert jobs[0][9] == 200.0


def test_rls_hides_other_tenants_ledger_rows(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        ledger_db.upsert_ledger_wallet_transactions([(
            "character", ALICE, 0, 1, DATE1, TYPE_ID, LOCATION_ID,
            1.0, 1, True, 1, None, None, None,
        )])
        assert ledger_db.count_ledger_rows("ledger_wallet_transactions", "character", ALICE) == 1
    with storage.tenant_context(b):
        assert ledger_db.count_ledger_rows("ledger_wallet_transactions", "character", ALICE) == 0
        ledger_db.upsert_ledger_wallet_transactions([(
            "character", ALICE, 0, 1, DATE1, TYPE_ID, LOCATION_ID,
            2.0, 2, False, 2, None, None, None,
        )])
        assert ledger_db.count_ledger_rows("ledger_wallet_transactions", "character", ALICE) == 1
    with storage.tenant_context(a):
        rows = ledger_db.list_ledger_wallet_transactions("character", ALICE)
        assert len(rows) == 1
        assert rows[0][7] == 1.0  # unit_price from tenant A


def test_share_revoke_hides_archive_rows(tenant):
    _share(ALICE, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([(
        "character", ALICE, 0, 7, DATE1, TYPE_ID, LOCATION_ID,
        1.0, 1, True, 7, None, None, None,
    )])
    assert ledger_access.count_archived_for_production(
        "ledger_wallet_transactions", "character", ALICE,
    ) == 1
    assert len(ledger_access.list_wallet_transactions_for_production("character", ALICE)) == 1
    _revoke(ALICE, "wallet")
    assert ledger_access.count_archived_for_production(
        "ledger_wallet_transactions", "character", ALICE,
    ) == 0
    assert ledger_access.list_wallet_transactions_for_production("character", ALICE) == []
    # Rows still physically present.
    assert ledger_db.count_ledger_rows("ledger_wallet_transactions", "character", ALICE) == 1
    _share(ALICE, "wallet")
    assert ledger_access.count_archived_for_production(
        "ledger_wallet_transactions", "character", ALICE,
    ) == 1


def test_gap_detection_records_when_sync_oldest_is_newer_than_archive(tenant):
    # Seed an old archived journal entry.
    ledger_db.upsert_ledger_wallet_journal([(
        "character", ALICE, 0, 1, DATE1, "brokers_fee", -10.0,
        None, None, None, None, None, None, None,
    )])
    # Sync whose oldest entry is newer than DATE1 -> gap.
    journal = [
        {"id": 10, "date": DATE2, "ref_type": "brokers_fee", "amount": -5.0},
        {"id": 11, "date": "2026-10-11T12:00:00+00:00", "ref_type": "brokers_fee", "amount": -5.0},
    ]
    ledger_ingest.archive_wallet(
        owner_type="character", owner_id=ALICE, txn_rows=[], journal_raw=journal,
    )
    gaps = ledger_db.list_archive_gaps("character", ALICE)
    assert len(gaps) == 1
    assert gaps[0][0] == "wallet_journal"
    # Resync with overlapping history must not invent another gap for the same window.
    ledger_ingest.archive_wallet(
        owner_type="character", owner_id=ALICE, txn_rows=[], journal_raw=journal,
    )
    assert len(ledger_db.list_archive_gaps("character", ALICE)) == 1


def test_production_archive_retention_requires_production_grant(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant", lambda t: ["trading"])
    assert tenant_eligibility.production_archive_retention("t") is False
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant", lambda t: ["production"])
    assert tenant_eligibility.production_archive_retention("t") is True
    assert "wallet" in tenant_eligibility.PRODUCTION_ARCHIVE_KINDS
    assert "industry_jobs" in tenant_eligibility.PRODUCTION_ARCHIVE_KINDS
    assert "market_orders" in tenant_eligibility.PRODUCTION_ARCHIVE_KINDS
    assert "contracts" in tenant_eligibility.PRODUCTION_ARCHIVE_KINDS


def test_pending_due_kinds_filter(tenant, monkeypatch):
    from eve_trader.esi_data import orchestrator

    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) VALUES "
            "('character', ?, 'wallet', 'production'), "
            "('character', ?, 'assets', 'production')",
            (ALICE, ALICE),
        )
    # Force both kinds due by clearing freshness.
    monkeypatch.setattr(orchestrator, "_kind_is_due", lambda *a, **k: True)
    all_due = orchestrator.pending_due(granted_tools={"production"})
    kinds = {row[2] for row in all_due}
    assert "wallet" in kinds and "assets" in kinds
    filtered = orchestrator.pending_due(
        granted_tools={"production"}, kinds=tenant_eligibility.PRODUCTION_ARCHIVE_KINDS,
    )
    assert {row[2] for row in filtered} == {"wallet"}


def test_market_orders_and_blueprints_upsert(tenant):
    ledger_ingest.archive_blueprints(
        owner_type="character", owner_id=ALICE,
        blueprints=[{
            "item_id": 1, "type_id": 999, "location_id": LOCATION_ID,
            "material_efficiency": 10, "time_efficiency": 20, "runs": -1, "quantity": -1,
        }],
    )
    ledger_ingest.archive_blueprints(
        owner_type="character", owner_id=ALICE,
        blueprints=[{
            "item_id": 1, "type_id": 999, "location_id": LOCATION_ID,
            "material_efficiency": 8, "time_efficiency": 16, "runs": -1, "quantity": -1,
        }],
    )
    assert ledger_db.count_ledger_rows("ledger_blueprints", "character", ALICE) == 1
    with storage.connect() as conn:
        me = conn.execute(
            "SELECT material_efficiency FROM ledger_blueprints WHERE item_id = 1",
        ).fetchone()[0]
    assert me == 8  # last-seen updates

    ledger_ingest.archive_market_orders(
        owner_type="character", owner_id=ALICE,
        orders=[{
            "order_id": 42, "type_id": TYPE_ID, "location_id": LOCATION_ID,
            "region_id": 10000002, "is_buy_order": False, "price": 100.0,
            "volume_total": 10, "volume_remain": 3, "issued": DATE1, "state": "expired",
        }],
    )
    assert ledger_db.count_ledger_rows("ledger_market_orders", "character", ALICE) == 1

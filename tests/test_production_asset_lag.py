"""Jobs fresher than assets (asset lag): ESI caches industry jobs ~5 min but
assets ~60 min, so a delivered job can leave the job snapshot before its
output reaches the asset snapshot. engine._asset_lag_adjustments keeps
counting that output (and takes out the materials of jobs started since the
asset snapshot) until a newer asset sync shows the real state."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import storage
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.realized import db as ledger_db

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

CORP = 98000101
CHAR = 91000101
BP, PRODUCT, MATERIAL = 7_900_001, 7_900_002, 7_900_003
T2_BP = 7_900_004
STATION, OTHER_STATION = 1_040_000_000_001, 1_040_000_000_002
ASSETS_AS_OF = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean(tenant):  # noqa: F811
    pg_helpers.wipe_tables(
        "ledger_industry_jobs", "ledger_job_inputs", "esi_freshness",
        "character_industry_jobs", "corp_industry_jobs",
    )
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO sde_blueprint_products (blueprint_type_id, activity_id, product_type_id, quantity) "
            "VALUES (%s, 1, %s, 10) ON CONFLICT DO NOTHING", (BP, PRODUCT))
    engine.invalidate_asset_lag_cache(all_tenants=True)
    yield
    engine.invalidate_asset_lag_cache(all_tenants=True)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _job(job_id, *, status, start, completed=None, owner=("corporation", CORP), activity=1,
         bp=BP, product=PRODUCT, runs=3, successful=None, station=STATION):
    owner_type, owner_id = owner
    ledger_db.upsert_ledger_industry_jobs([(
        owner_type, owner_id, job_id, activity, None, bp, product, runs, None, successful,
        None, station, station, station, None,
        _iso(start), _iso(start + timedelta(hours=1)), _iso(completed) if completed else None,
        status, None,
    )])


def _assets_synced(as_of=ASSETS_AS_OF, owner=("corporation", CORP), kind="assets"):
    storage.upsert_esi_freshness(owner[0], owner[1], kind, success=True,
                                 now=_iso(as_of + timedelta(minutes=5)), data_as_of=as_of)


def _input(job_id, type_id, qty, owner=("corporation", CORP)):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO ledger_job_inputs (owner_type, owner_id, job_id, type_id, quantity, source) "
            "VALUES (?,?,?,?,?, 'test')", (owner[0], owner[1], job_id, type_id, qty))


def test_delivered_after_asset_snapshot_counts_until_assets_catch_up():
    _assets_synced()
    _job(1, status="delivered", start=ASSETS_AS_OF - timedelta(hours=2),
         completed=ASSETS_AS_OF + timedelta(minutes=10))
    _job(2, status="delivered", start=ASSETS_AS_OF - timedelta(hours=3),
         completed=ASSETS_AS_OF - timedelta(minutes=10))  # already in the assets

    assert storage.delivered_job_outputs_after_assets([], [CORP]) == {(PRODUCT, STATION): 30.0}

    _assets_synced(ASSETS_AS_OF + timedelta(hours=1))
    assert storage.delivered_job_outputs_after_assets([], [CORP]) == {}


def test_job_still_in_the_snapshot_is_not_counted_twice():
    _assets_synced()
    _job(1, status="delivered", start=ASSETS_AS_OF, completed=ASSETS_AS_OF + timedelta(minutes=10))
    storage.replace_industry_jobs(
        "corp_industry_jobs",
        [(1, 1, BP, PRODUCT, 3, STATION, "active", _iso(ASSETS_AS_OF), _iso(ASSETS_AS_OF), None, "x")],
        owner_corporation_id=CORP)
    assert storage.delivered_job_outputs_after_assets([], [CORP]) == {}


def test_owner_without_asset_sync_or_not_requested_gets_no_correction():
    _job(1, status="delivered", start=ASSETS_AS_OF, completed=ASSETS_AS_OF + timedelta(minutes=10))
    assert storage.delivered_job_outputs_after_assets([], [CORP]) == {}  # no freshness row
    _assets_synced()
    assert storage.delivered_job_outputs_after_assets([CHAR], []) == {}  # other owner
    assert storage.delivered_job_outputs_after_assets([], [CORP], exclude_location_ids=[STATION]) == {}


def test_inputs_of_jobs_started_after_the_asset_snapshot_are_taken_out():
    _assets_synced()
    _job(1, status="active", start=ASSETS_AS_OF + timedelta(minutes=5))
    _job(2, status="cancelled", start=ASSETS_AS_OF + timedelta(minutes=6), station=OTHER_STATION)
    _job(3, status="active", start=ASSETS_AS_OF - timedelta(minutes=5))  # already out of the assets
    _input(1, MATERIAL, 100.0)
    _input(2, MATERIAL, 40.0)
    _input(3, MATERIAL, 999.0)

    assert storage.started_job_inputs_after_assets([], [CORP]) == {
        (MATERIAL, STATION): 100.0, (MATERIAL, OTHER_STATION): 40.0}


def test_delivered_inventions_count_until_the_blueprint_snapshot_shows_them():
    _assets_synced(kind="blueprints")
    _job(1, status="delivered", activity=8, bp=1, product=T2_BP, runs=5, successful=2,
         start=ASSETS_AS_OF - timedelta(hours=5), completed=ASSETS_AS_OF + timedelta(minutes=1))
    assert storage.delivered_inventions_after_blueprints(T2_BP, [], [CORP]) == 2.0
    _assets_synced(ASSETS_AS_OF + timedelta(hours=1), kind="blueprints")
    assert storage.delivered_inventions_after_blueprints(T2_BP, [], [CORP]) == 0.0


def test_current_stock_and_on_hand_apply_the_lag(monkeypatch):
    _assets_synced()
    _job(1, status="delivered", start=ASSETS_AS_OF - timedelta(hours=2),
         completed=ASSETS_AS_OF + timedelta(minutes=10))
    _job(2, status="active", start=ASSETS_AS_OF + timedelta(minutes=5))
    _input(2, MATERIAL, 100.0)
    monkeypatch.setattr(engine, "shared_production_owner_ids", lambda kind: ([], [CORP]))
    monkeypatch.setattr(engine, "production_excluded_location_ids", lambda: [])
    stock = {PRODUCT: 5.0, MATERIAL: 60.0}
    monkeypatch.setattr(engine, "_stock_at_location",
                        lambda type_id, location_id, **k: stock.get(type_id, 0.0))
    monkeypatch.setattr(engine, "_esi_incoming_industry_qty", lambda type_id: {"runs": 0, "jobs": 0})
    monkeypatch.setattr(storage, "manual_incoming_qty", lambda type_id: 0.0)
    cfg = ProductionConfig()

    assert engine._current_stock(PRODUCT, {}, cfg, (BP, 1, 10.0)) == 35.0
    assert engine._stock_on_hand(PRODUCT, {}, cfg) == 35.0
    # 60 in the (old) asset snapshot, 100 already consumed: never negative.
    assert engine._stock_on_hand(MATERIAL, {}, cfg) == 0.0


def test_no_shared_owner_means_no_correction(monkeypatch):
    _assets_synced()
    _job(1, status="delivered", start=ASSETS_AS_OF, completed=ASSETS_AS_OF + timedelta(minutes=10))
    # Jobs shared, assets not: the output would never show in stock anyway.
    monkeypatch.setattr(engine, "shared_production_owner_ids",
                        lambda kind: ([], [CORP]) if kind == "industry_jobs" else ([], []))
    assert engine._asset_lag_adjustments() == ({}, {})

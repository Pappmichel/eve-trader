"""Stock that covers a stock target's home/Jita listing shortfall is held back
from other jobs' material needs, in both planners (not only the backup part).

Uses the synthetic BOM of test_special_orders_integration: one SHIP (ordered)
needs 10 COMPONENT; COMPONENT is also a stock target.
"""
import pytest

from eve_trader import storage
from eve_trader.production import engine

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401
from .test_special_orders_integration import (
    COMPONENT, SHIP, _asset_cfg, _asset_setup, _isolate, _jobs, _runs,  # noqa: F401
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _no_listings(monkeypatch):
    # Nothing is listed anywhere yet: the whole listing target is short.
    monkeypatch.setattr(engine, "_sell_order_qty_at_location", lambda type_id, location_id: 0.0)
    monkeypatch.setattr(engine, "_sell_order_qty_in_region", lambda type_id, region_id: 0.0)
    monkeypatch.setattr(storage, "manual_listed_stock_qty", lambda type_id, market: 0.0)


def _both(monkeypatch, target, stock):
    _asset_setup(monkeypatch, stock_targets=[(COMPONENT, "Component") + target],
                 orders=[("o1", None, SHIP, "Ship", 1.0)], stock={COMPONENT: stock})
    cfg = _asset_cfg(home_location_id=60003760)
    asset_jobs = _jobs(engine.plan_asset_optimized(cfg))
    prod_runs = _runs(engine.plan_production(cfg))
    return asset_jobs, prod_runs


def test_stock_covering_home_listing_target_is_not_used_as_material(monkeypatch, tenant):
    # Home target 12, 12 on hand: all 12 are earmarked for listing, so the
    # Ship's 10 Component must be built. Before, only the backup (0) was held
    # back and the Ship ate the listing stock.
    asset_jobs, prod_runs = _both(monkeypatch, target=(0, 12, 0), stock=12.0)
    assert asset_jobs[COMPONENT].job_runs == 10
    assert prod_runs[COMPONENT] == 10
    # Readiness still sees the physical units.
    assert asset_jobs[SHIP].runs_ready_now == 1


def test_stock_covering_jita_listing_target_is_not_used_as_material(monkeypatch, tenant):
    asset_jobs, prod_runs = _both(monkeypatch, target=(0, 0, 12), stock=12.0)
    assert asset_jobs[COMPONENT].job_runs == 10
    assert prod_runs[COMPONENT] == 10


def test_backup_only_target_behaves_as_before(monkeypatch, tenant):
    asset_jobs, prod_runs = _both(monkeypatch, target=(10, 0, 0), stock=10.0)
    assert asset_jobs[COMPONENT].job_runs == 10
    assert prod_runs[COMPONENT] == 10


def test_surplus_beyond_all_targets_stays_free_for_jobs(monkeypatch, tenant):
    # Home target 5, 12 on hand: 5 are earmarked, 7 are free for the Ship's
    # 10 Component, so only 3 more are built.
    asset_jobs, prod_runs = _both(monkeypatch, target=(0, 5, 0), stock=12.0)
    assert asset_jobs[COMPONENT].job_runs == 3
    assert prod_runs[COMPONENT] == 3


def test_backup_home_and_jita_all_count_and_are_capped_at_stock(monkeypatch, tenant):
    # backup 4 + home 4 + jita 4 = 12 earmarked of 12 on hand -> build all 10.
    asset_jobs, prod_runs = _both(monkeypatch, target=(4, 4, 4), stock=12.0)
    assert asset_jobs[COMPONENT].job_runs == 10
    assert prod_runs[COMPONENT] == 10
    # Only 6 on hand: everything is earmarked (never more than owned).
    asset_jobs, prod_runs = _both(monkeypatch, target=(4, 4, 4), stock=6.0)
    assert asset_jobs[COMPONENT].job_runs == 10 + 6
    assert prod_runs[COMPONENT] == 10 + 6


def test_stock_allocation_reserve_matches_total_missing(monkeypatch):
    cfg = _asset_cfg(home_location_id=60003760)
    for backup, home, jita, stock in ((0, 12, 0, 12.0), (10, 0, 0, 4.0), (3, 5, 7, 20.0), (3, 5, 7, 9.0)):
        missing, reserved = engine._stock_allocation(COMPONENT, backup, home, jita, stock, cfg)
        assert missing == engine._total_missing(COMPONENT, backup, home, jita, stock, cfg)
        assert 0 <= reserved <= stock
        # every unit is either short or reserved against the total target
        assert missing + reserved == pytest.approx(backup + home + jita)

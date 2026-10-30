"""plan_asset_optimized's doctrine pass (user request 2026-10-30): stock
targets that are part of an active doctrine fitting claim stock, readiness
and slots after special orders and before every other stock target.

Reuses the synthetic BOM of test_special_orders_integration (SHIP and OTHER
each need 10x COMPONENT per unit; FAST_A / FAST_B have no materials).
"""
import pytest

from eve_trader import storage, tenant_eligibility
from eve_trader.production import engine

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401
from .test_special_orders_integration import (  # noqa: F401
    COMPONENT, FAST_A, FAST_B, OTHER, SHIP, _asset_cfg, _asset_setup, _isolate, _jobs,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def _doctrine(monkeypatch, type_ids):
    monkeypatch.setattr(engine, "_doctrine_type_ids", lambda: set(type_ids))


def test_doctrine_target_gets_scarce_stock_before_a_smaller_claim(monkeypatch, tenant):
    # OTHER (claims 10 Component) and SHIP (claims 20) compete for 10
    # Component on hand. Smallest-claim-first gives it to OTHER; with SHIP in
    # a doctrine, SHIP takes it.
    targets = [(OTHER, "Other", 1, 0, 0), (SHIP, "Ship", 2, 0, 0)]
    _asset_setup(monkeypatch, stock_targets=targets, orders=[], stock={COMPONENT: 10.0})
    _doctrine(monkeypatch, [])
    plain = _jobs(engine.plan_asset_optimized(_asset_cfg()))
    assert (plain[OTHER].runs_ready_now, plain[SHIP].runs_ready_now) == (1, 0)

    _doctrine(monkeypatch, [SHIP])
    jobs = _jobs(engine.plan_asset_optimized(_asset_cfg()))

    assert (jobs[SHIP].runs_ready_now, jobs[SHIP].doctrine_ready_runs) == (1, 1)
    assert (jobs[OTHER].runs_ready_now, jobs[OTHER].doctrine_ready_runs) == (0, 0)
    # Priority only reorders who gets the stock; what is planned is the same.
    assert {t: j.job_runs for t, j in jobs.items()} == {t: j.job_runs for t, j in plain.items()}


def test_doctrine_share_reaches_components_and_buffer_is_applied_once(monkeypatch, tenant):
    targets = [(OTHER, "Other", 1, 0, 0), (SHIP, "Ship", 1, 0, 0)]
    _asset_setup(monkeypatch, stock_targets=targets, orders=[])
    cfg = _asset_cfg(component_overbuild=0.7)
    _doctrine(monkeypatch, [])
    plain = _jobs(engine.plan_asset_optimized(cfg))

    _doctrine(monkeypatch, [SHIP])
    jobs = _jobs(engine.plan_asset_optimized(cfg))

    assert {t: j.job_runs for t, j in jobs.items()} == {t: j.job_runs for t, j in plain.items()}
    assert (jobs[SHIP].doctrine_runs, jobs[OTHER].doctrine_runs) == (1, 0)
    # SHIP's 10 Component (plus its buffer) come from the doctrine pass.
    assert 10 <= jobs[COMPONENT].doctrine_runs < jobs[COMPONENT].job_runs


def test_doctrine_item_without_stock_target_gets_no_priority(monkeypatch, tenant):
    _asset_setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)], orders=[])
    _doctrine(monkeypatch, [SHIP])

    jobs = _jobs(engine.plan_asset_optimized(_asset_cfg()))

    assert SHIP not in jobs
    assert all(j.doctrine_runs == 0 for j in jobs.values())


def test_fewer_slots_than_jobs_starts_doctrine_jobs_after_orders(monkeypatch, tenant):
    # One free slot: FAST_B is longer (would start first by job time), but
    # FAST_A is a doctrine item.
    targets = [(FAST_A, "A", 1, 0, 0), (FAST_B, "B", 20, 0, 0)]
    _asset_setup(monkeypatch, stock_targets=targets, orders=[], free_slots=1)
    _doctrine(monkeypatch, [FAST_A])

    jobs = _jobs(engine.plan_asset_optimized(_asset_cfg()))

    assert (jobs[FAST_A].recommended_slots, jobs[FAST_B].recommended_slots) == (1, 0)


def test_allocate_slots_tiers_order_then_doctrine_then_rest():
    # (type_id, ready_seconds, runs, unlock, coverage, order_runs, doctrine_runs)
    claims = [(1, 900.0, 5, 50.0, 0.1, 0, 0), (2, 10.0, 5, 0.0, 0.9, 0, 3), (3, 20.0, 5, 0.0, 0.5, 2, 0)]
    assert engine._allocate_slots_min_makespan(claims, 2) == {1: 0, 2: 1, 3: 1}
    assert engine._allocate_slots_min_makespan(claims, 1) == {1: 0, 2: 0, 3: 1}


def test_allocate_slots_days_target_tops_up_doctrine_runs_before_other_jobs_start():
    # One-day runs, 1-day target, 4 slots: job 1 has 3 doctrine runs and gets
    # the 3 slots they need before job 2 gets its one slot.
    day = 86400.0
    claims = [(1, 6 * day, 6, 0.0, None, 0, 3), (2, 20 * day, 20, 0.0, None, 0, 0)]
    assert engine._allocate_slots_min_makespan(claims, 4, target_seconds=day) == {1: 3, 2: 1}
    # Order and doctrine runs on one job count together.
    claims = [(1, 6 * day, 6, 0.0, None, 1, 2), (2, 20 * day, 20, 0.0, None, 0, 0)]
    assert engine._allocate_slots_min_makespan(claims, 4, target_seconds=day) == {1: 3, 2: 1}


def test_doctrine_type_ids_needs_the_doctrine_grant(monkeypatch, tenant):
    monkeypatch.setattr(storage, "list_active_fittings",
                        lambda: [("f1", "d1", "Fit", None, SHIP)])
    monkeypatch.setattr(storage, "load_fitting_items",
                        lambda fitting_id: [(1, "low", OTHER, 1, False), (2, "cargo", None, 5, False)])
    monkeypatch.setattr(tenant_eligibility, "granted_tools", lambda tenant_id: {"production", "doctrine"})
    assert engine._doctrine_type_ids() == {SHIP, OTHER}

    monkeypatch.setattr(tenant_eligibility, "granted_tools", lambda tenant_id: {"production"})
    assert engine._doctrine_type_ids() == set()

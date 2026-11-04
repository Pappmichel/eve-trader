"""plan_asset_optimized with ProductionConfig.asset_plan_ready_at_build_location
(user request 2026-11-04): "Ready Now" only counts stock at the structure
set for the job's category on the Logistics page; a category without a
structure counts every location; sizing does not change.

Reuses the synthetic BOM of test_special_orders_integration (SHIP and OTHER
each need 10x COMPONENT per unit).
"""
import pytest

from eve_trader import storage
from eve_trader.production import engine

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401
from .test_special_orders_integration import (  # noqa: F401
    COMPONENT, OTHER, SHIP, _asset_cfg, _asset_setup, _isolate, _jobs,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

EQUIPMENT_STRUCTURE, SHIP_STRUCTURE = 1_050_000_000_001, 1_050_000_000_002
_CATEGORY = {OTHER: "Equipment", SHIP: "Small Ships", COMPONENT: "Advanced Components"}


def _locations(monkeypatch, category_locations, at_location):
    """at_location: {(type_id, location_id): on-hand units there}."""
    monkeypatch.setattr(storage, "load_category_locations", lambda: dict(category_locations))
    monkeypatch.setattr(engine, "job_category", lambda type_id: _CATEGORY.get(type_id))
    monkeypatch.setattr(engine, "_stock_on_hand_at",
                        lambda type_id, location_id, cfg: at_location.get((type_id, location_id), 0.0))


def _plan(on):
    return _jobs(engine.plan_asset_optimized(_asset_cfg(asset_plan_ready_at_build_location=on)))


def test_input_at_another_structure_blocks_only_in_location_mode(monkeypatch, tenant):
    _asset_setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)], orders=[],
                 stock={COMPONENT: 10.0})
    _locations(monkeypatch, {"Equipment": EQUIPMENT_STRUCTURE}, {})

    plain = _plan(False)
    assert plain[OTHER].runs_ready_now == 1

    jobs = _plan(True)
    assert jobs[OTHER].runs_ready_now == 0
    [blocker] = jobs[OTHER].blockers
    assert (blocker.type_id, blocker.covered, blocker.elsewhere) == (COMPONENT, 0.0, 10.0)
    # Sizing is the same in both modes.
    assert {t: j.job_runs for t, j in jobs.items()} == {t: j.job_runs for t, j in plain.items()}


def test_input_at_the_build_structure_is_ready(monkeypatch, tenant):
    _asset_setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)], orders=[],
                 stock={COMPONENT: 10.0})
    _locations(monkeypatch, {"Equipment": EQUIPMENT_STRUCTURE},
               {(COMPONENT, EQUIPMENT_STRUCTURE): 10.0})
    assert _plan(True)[OTHER].runs_ready_now == 1


def test_category_without_a_structure_counts_every_location(monkeypatch, tenant):
    _asset_setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)], orders=[],
                 stock={COMPONENT: 10.0})
    _locations(monkeypatch, {"Small Ships": SHIP_STRUCTURE}, {})
    jobs = _plan(True)
    assert jobs[OTHER].runs_ready_now == 1
    assert jobs[OTHER].blockers == []


def test_two_structures_never_share_the_same_units(monkeypatch, tenant):
    # 10 Component in total; the per-structure reads both claim 10. The
    # total cap stops the second job from being ready on the same units.
    _asset_setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0), (SHIP, "Ship", 1, 0, 0)],
                 orders=[], stock={COMPONENT: 10.0})
    _locations(monkeypatch, {"Equipment": EQUIPMENT_STRUCTURE, "Small Ships": SHIP_STRUCTURE},
               {(COMPONENT, EQUIPMENT_STRUCTURE): 10.0, (COMPONENT, SHIP_STRUCTURE): 10.0})
    jobs = _plan(True)
    assert jobs[OTHER].runs_ready_now + jobs[SHIP].runs_ready_now == 1


def test_readiness_ledger_without_location_matches_the_old_rule(monkeypatch):
    monkeypatch.setattr(engine, "_stock_on_hand", lambda type_id, manual, cfg: 10.0)
    ledger = engine._ReadinessLedger({}, _asset_cfg(), {COMPONENT: 3.0})
    assert ledger.available(COMPONENT, None) == 7.0
    ledger.claim(COMPONENT, None, 5.0)
    assert ledger.available(COMPONENT, None) == 2.0
    assert ledger.elsewhere(COMPONENT, None, 4.0) == 0.0

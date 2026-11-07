"""Capacity-mode asset plan continues the plan's own ledgers.

Reaction jobs' readiness and their non-reaction inputs used to start from
fresh ledgers, so stock an order or stock-target job had already been given
was handed out a second time. Reuses the BOM of test_reaction_capacity_wiring
and adds COMPONENT3 (3), a stock target built straight from raw inputs.
"""
from __future__ import annotations

import math

import pytest

from eve_trader import storage
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.constants import ACTIVITY_MANUFACTURING

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401
from .test_reaction_capacity_wiring import (  # noqa: F401
    _BP, _MATERIALS, _NAMES, _activity, _fake_ctx, _noop_save_latest_buy_list,
    _stub_shared_production_owner_ids, _stub_w2_sales_signals, reaction_bom,
)
from .test_storage_production_buy_list import _apply_production_buy_list_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

COMPONENT, COMPONENT3, REACTION_R, FUEL, MOON = 1, 3, 10, 20, 30


@pytest.fixture
def with_component3(monkeypatch, reaction_bom):
    def _mats(moon_qty=0, fuel_qty=0):
        mats = []
        if moon_qty:
            mats.append((MOON, moon_qty))
        if fuel_qty:
            mats.append((FUEL, fuel_qty))
        return mats

    def install(moon_qty=0, fuel_qty=0):
        monkeypatch.setitem(_BP, COMPONENT3, (103, ACTIVITY_MANUFACTURING, 1.0))
        monkeypatch.setitem(_MATERIALS, 103, _mats(moon_qty, fuel_qty))
        monkeypatch.setitem(_NAMES, COMPONENT3, "Component3")
        monkeypatch.setattr(
            engine, "classify_activity",
            lambda type_id: ("Tech I", _BP[type_id]) if type_id == COMPONENT3 else _activity(type_id))
        job_category = engine.job_category
        monkeypatch.setattr(
            engine, "job_category",
            lambda type_id: "Advanced Components" if type_id == COMPONENT3 else job_category(type_id))
    return install


def _stocks(monkeypatch, stock):
    monkeypatch.setattr(engine, "_current_stock", lambda type_id, *a, **k: stock.get(type_id, 0.0))
    monkeypatch.setattr(engine, "_stock_on_hand", lambda type_id, *a, **k: stock.get(type_id, 0.0))


def _cfg(**overrides):
    return ProductionConfig(
        **{"component_overbuild": 0.0, "reaction_capacity_enabled": True, "reaction_slot_override": 5, **overrides})


def _targets():
    return [(COMPONENT, "Component", 10, 0, 0), (COMPONENT3, "Component3", 1, 0, 0)]


@pg_helpers.postgres_required()
def test_reaction_job_is_not_ready_on_units_a_stock_target_job_already_claimed(
        monkeypatch, with_component3, tenant):
    # Component3 needs 100 Moon and is Ready on the 100 on hand; the reaction
    # (100 Moon per run) must not be Ready on the same pile.
    with_component3(moon_qty=100)
    _stocks(monkeypatch, {MOON: 100.0, FUEL: 1000.0})
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(_targets()))

    asset = engine.plan_asset_optimized(_cfg())

    jobs = {j.type_id: j for j in asset["jobs"]}
    assert jobs[COMPONENT3].runs_ready_now == 1
    assert jobs[REACTION_R].job_runs >= 1
    assert jobs[REACTION_R].runs_ready_now == 0
    moon_blocker = next(b for b in jobs[REACTION_R].blockers if b.type_id == MOON)
    assert moon_blocker.covered == pytest.approx(0.0)


@pg_helpers.postgres_required()
def test_reaction_job_keeps_readiness_when_nothing_else_claims_the_stock(
        monkeypatch, with_component3, tenant):
    # Same stock, but Component3 does not touch Moon: the reaction is Ready.
    with_component3(fuel_qty=1)
    _stocks(monkeypatch, {MOON: 100.0, FUEL: 1000.0})
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(_targets()))

    asset = engine.plan_asset_optimized(_cfg())

    jobs = {j.type_id: j for j in asset["jobs"]}
    assert jobs[REACTION_R].runs_ready_now == 1


@pg_helpers.postgres_required()
def test_reaction_inputs_are_not_netted_against_stock_already_consumed(
        monkeypatch, with_component3, tenant):
    # Component3 consumes the whole 1000 Fuel on hand. The reaction's own Fuel
    # need (5 per run) must then be built, not netted against the same 1000.
    with_component3(fuel_qty=1000)
    _stocks(monkeypatch, {FUEL: 1000.0})
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(_targets()))

    asset = engine.plan_asset_optimized(_cfg())

    jobs = {j.type_id: j for j in asset["jobs"]}
    reaction_runs = jobs[REACTION_R].job_runs
    assert reaction_runs >= 1
    assert FUEL in jobs
    # One Fuel run makes 40 units; the reaction needs 5 per run.
    assert jobs[FUEL].quantity >= 5.0 * reaction_runs
    assert jobs[FUEL].job_runs == math.ceil(5.0 * reaction_runs / 40.0)


@pg_helpers.postgres_required()
def test_reaction_inputs_in_stock_still_cover_the_reaction_when_unclaimed(
        monkeypatch, with_component3, tenant):
    # Component3 takes only 400 of the 1000 Fuel; 600 are free for the
    # reaction, so no Fuel job is planned (nothing is double counted either way).
    with_component3(fuel_qty=400)
    _stocks(monkeypatch, {FUEL: 1000.0})
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(_targets()))

    asset = engine.plan_asset_optimized(_cfg())

    assert FUEL not in {j.type_id for j in asset["jobs"]}


@pg_helpers.postgres_required()
def test_switch_off_asset_plan_is_unaffected(monkeypatch, with_component3, tenant):
    with_component3(moon_qty=100)
    _stocks(monkeypatch, {MOON: 100.0, FUEL: 1000.0})
    monkeypatch.setattr(engine, "_PlanContext", _fake_ctx(_targets()))

    asset = engine.plan_asset_optimized(_cfg(reaction_capacity_enabled=False))

    assert "reaction_capacity" not in asset
    jobs = {j.type_id: (j.job_runs, j.runs_ready_now) for j in asset["jobs"]}
    # Hand-derived: Component3 1 run (Ready on the 100 Moon); Component needs
    # 10 reaction product -> 1 reaction run, which needs 100 Moon (taken) and
    # 5 Fuel (on hand): not Ready.
    assert jobs[COMPONENT3] == (1, 1)
    assert jobs[REACTION_R] == (1, 0)

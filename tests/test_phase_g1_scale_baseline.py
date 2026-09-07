"""Phase G.1 — scale baseline (measure, do not optimize). Timings are docs, not gates."""
from __future__ import annotations

import time
import tracemalloc

import pytest

from eve_trader.production import actions

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    pg_helpers.wipe_tables("character_assets")
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


def _timed(fn):
    tracemalloc.start()
    start = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - start) * 1000
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed_ms, peak / 1024


@pytest.mark.parametrize("n", [1, 10, 50, 100])
def test_pooled_scale_semantics(order_sde, n):
    ids = []
    _, create_ms, _peak = _timed(lambda: [
        ids.append(actions.do_create_special_order(
            [{"type_id": fx.FINISHED_A, "quantity": 2.0}])["order_id"])
        for _ in range(n)
    ])
    _, list_ms, _ = _timed(actions.do_list_special_orders)
    one_plan, one_ms, _ = _timed(lambda: actions.do_compute_special_order(ids[0], cfg=fx.widget_cfg()))
    combined, comb_ms, peak_kib = _timed(
        lambda: actions.do_compute_combined_special_orders(ids, net_against_stock=False, cfg=fx.widget_cfg()))
    _, audit_ms, _ = _timed(actions.do_audit_special_orders)
    print(f"n={n} create_ms={create_ms:.2f} list_ms={list_ms:.2f} one_ms={one_ms:.2f} "
          f"combined_ms={comb_ms:.2f} audit_ms={audit_ms:.2f} peak_kib={peak_kib:.1f}")
    assert fx.lines(combined) == {fx.FINISHED_A: 2.0 * n}
    assert fx.runs(combined)[fx.FINISHED_A] == 2 * n
    assert fx.runs(one_plan)[fx.FINISHED_A] == 2
    assert create_ms < 60_000
    assert comb_ms < 60_000


def test_hangar_net_at_scale_does_not_net_top_level(order_sde):
    from eve_trader import storage
    storage.replace_assets("character_assets", [
        (1, fx.FINISHED_A, 60003760, "Hangar", 10_000, 0, "Test Character"),
    ])
    ids = [
        actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
        for _ in range(10)
    ]
    plan = actions.do_compute_combined_special_orders(ids, net_against_stock=True, cfg=fx.widget_cfg())
    assert fx.runs(plan)[fx.FINISHED_A] == 100

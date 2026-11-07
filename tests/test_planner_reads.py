"""Per-run planner_reads memo: query count independent of type count, a
mutation between runs is visible, nested/tenant/thread behaviour, and the
stock filters still apply inside the scope."""
from __future__ import annotations

import contextvars
import threading

import pytest

from eve_trader import storage
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig

from . import pg_helpers
from .pg_helpers import tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

MIN_A, MIN_B = 96001, 96002
BASE = 96100
LOCATION = 60003760
CHAR_ID = 42


def _type(type_id, name, published=1):
    return (type_id, 18, name, 1.0, published, None, 0, None, 1)


def _empty_sde(**kwargs):
    base = dict(types=[], groups=[], market_groups=[], blueprint_time=[],
                blueprint_materials=[], blueprint_products=[])
    base.update(kwargs)
    storage.replace_sde_data(**base)


def _seed_products(n: int) -> list[int]:
    types = [_type(MIN_A, "Mineral A"), _type(MIN_B, "Mineral B")]
    materials, products, times = [], [], []
    finished = []
    for i in range(n):
        prod = BASE + i * 10
        bp = BASE + i * 10 + 2
        finished.append(prod)
        types += [_type(prod, f"Product {i}"), _type(bp, f"Product {i} Blueprint")]
        materials += [(bp, 1, MIN_A, 5), (bp, 1, MIN_B, 3)]
        products += [(bp, 1, prod, 1.0)]
        times += [(bp, 1, 600.0)]
    _empty_sde(types=types, blueprint_materials=materials,
               blueprint_products=products, blueprint_time=times)
    return finished


@pytest.fixture(autouse=True)
def _clean(tenant, monkeypatch):
    _empty_sde()
    # Unfiltered owners so asset rows seeded without owner_character_id still
    # count (same convention as other engine stock tests).
    monkeypatch.setattr(engine, "shared_production_owner_ids",
                        lambda kind: (None, None))
    yield
    _empty_sde()
    engine.invalidate_excluded_locations_cache()
    engine.invalidate_asset_lag_cache(all_tenants=True)
    engine.invalidate_shared_production_owner_ids_cache(all_tenants=True)


@pytest.fixture
def query_counter(monkeypatch):
    counter = {"n": 0}
    real = storage.translate_placeholders

    def counting(sql):
        counter["n"] += 1
        return real(sql)

    monkeypatch.setattr(storage, "translate_placeholders", counting)
    return counter


@pytest.fixture
def cfg():
    return ProductionConfig(home_location_id=LOCATION)


def _touch_reads(products, cfg, query_counter):
    before = query_counter["n"]
    with engine.planner_reads():
        engine._prefetch_planner_reads(
            engine._structural_material_closure(products), cfg)
        manual = storage.load_manual_stock()
        for tid in list(products) + [MIN_A, MIN_B]:
            bp = storage.get_blueprint_for_product(tid)
            engine._current_stock(tid, manual, cfg, bp)
            if bp is not None:
                engine._owned_bpo_best_me_te(bp[0])
            engine._haul_volume(tid, cfg)
            engine._manual_bpc_cost_per_run(tid)
            engine._esi_incoming_industry_qty(tid)
            engine._manual_incoming_qty(tid)
    return query_counter["n"] - before


def test_planner_reads_query_count_independent_of_type_count(tenant, cfg, query_counter):
    small_products = _seed_products(5)
    storage.replace_assets("character_assets", [
        (i + 1, tid, LOCATION, "Hangar", 10, 0, "Alice", False)
        for i, tid in enumerate(small_products + [MIN_A, MIN_B])
    ])
    small = _touch_reads(small_products, cfg, query_counter)

    large_products = _seed_products(50)
    storage.replace_assets("character_assets", [
        (i + 1, tid, LOCATION, "Hangar", 10, 0, "Alice", False)
        for i, tid in enumerate(large_products + [MIN_A, MIN_B])
    ])
    large = _touch_reads(large_products, cfg, query_counter)

    assert large < small * 3, f"small={small} large={large}"


def test_second_run_sees_manual_stock_change(tenant, cfg):
    products = _seed_products(1)
    tid = products[0]
    storage.replace_assets("character_assets", [
        (1, tid, LOCATION, "Hangar", 0, 0, "Alice", False),
    ])
    with engine.planner_reads():
        engine._prefetch_planner_reads([tid], cfg)
        first = engine._stock_on_hand(tid, {}, cfg)
    storage.upsert_manual_stock(tid, 42.0, LOCATION)
    with engine.planner_reads():
        engine._prefetch_planner_reads([tid], cfg)
        second = engine._stock_on_hand(tid, storage.load_manual_stock(), cfg)
    assert first == 0.0
    assert second == 42.0


def test_fitted_items_excluded_inside_planner_reads(tenant, cfg):
    """Fitted rows (HiSlot0) are assembled_or_fitted; Hangar rows still count.
    Same figure with and without planner_reads."""
    products = _seed_products(1)
    tid = products[0]
    storage.replace_assets("character_assets", [
        (1, tid, LOCATION, "HiSlot0", 7, 0, "Alice", True),   # fitted - excluded
        (2, tid, LOCATION, "Hangar", 3, 0, "Alice", False),
    ])
    with engine.planner_reads():
        engine._prefetch_planner_reads([tid], cfg)
        inside = engine._stock_on_hand(tid, {}, cfg)
    outside = engine._stock_on_hand(tid, {}, cfg)
    assert inside == outside == 3.0


def test_nested_planner_reads_reuses_outer(tenant, cfg):
    products = _seed_products(1)
    tid = products[0]
    with engine.planner_reads() as outer:
        engine._prefetch_planner_reads([tid], cfg)
        with engine.planner_reads() as inner:
            assert inner is outer
            engine._stock_on_hand(tid, {}, cfg)
        assert any(tid in bag for bag in outer.esi_stock_all.values())


def test_worker_thread_does_not_see_parent_scope(tenant, cfg):
    products = _seed_products(1)
    tid = products[0]
    seen = {}

    def worker():
        seen["scope"] = engine._active_planner_reads()

    with engine.planner_reads():
        engine._prefetch_planner_reads([tid], cfg)
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    assert seen["scope"] is None


def test_copied_context_sees_parent_scope(tenant, cfg):
    products = _seed_products(1)
    tid = products[0]
    seen = {}

    def worker():
        seen["scope"] = engine._active_planner_reads()

    with engine.planner_reads() as scope:
        engine._prefetch_planner_reads([tid], cfg)
        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(worker))
        t.start()
        t.join()
        assert seen["scope"] is scope


def test_tenant_switch_bypasses_scope(tenant, cfg):
    products = _seed_products(1)
    tid = products[0]
    other = storage.create_tenant("other-planner-reads")
    with engine.planner_reads() as scope:
        engine._prefetch_planner_reads([tid], cfg)
        assert engine._active_planner_reads() is scope
        with storage.tenant_context(other):
            assert engine._active_planner_reads() is None
        assert engine._active_planner_reads() is scope


def test_prefetch_type_categories_one_query(tenant, query_counter):
    products = _seed_products(20)
    before = query_counter["n"]
    cats = storage.prefetch_type_categories(products)
    mid = query_counter["n"]
    storage.prefetch_type_categories(products)
    after = query_counter["n"]
    assert mid - before == 1
    assert after == mid
    assert all(tid in cats for tid in products)
    before2 = query_counter["n"]
    for tid in products:
        storage.get_type_category(tid)
    assert query_counter["n"] == before2

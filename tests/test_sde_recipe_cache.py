"""Recipe sidecars (storage.get_blueprint_for_product / get_blueprint_materials /
get_blueprint_time / find_invention_recipe_candidates_by_product_type_id /
get_product_quantity): bulk prefill, complete index, invalidation by an SDE
apply, and the planner's query budget.

SDE tables are global; every test seeds its own rows through
`replace_sde_data` (which also clears the caches) and removes them again.
"""
import threading

import pytest

from eve_trader import storage
from eve_trader.production import engine

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

MIN_A, MIN_B = 95001, 95002
BASE = 95100


def _type(type_id, name, published=1):
    return (type_id, 18, name, 1.0, published, None, 0, None, 1)


def _empty_sde(**kwargs):
    base = dict(types=[], groups=[], market_groups=[], blueprint_time=[],
                blueprint_materials=[], blueprint_products=[])
    base.update(kwargs)
    storage.replace_sde_data(**base)


def _seed_chain(n: int, quantity: float = 1.0) -> list[int]:
    """n finished products; each needs its own built component plus two shared
    minerals. Returns the finished product ids."""
    types = [_type(MIN_A, "Mineral A"), _type(MIN_B, "Mineral B")]
    materials, products, times = [], [], []
    finished = []
    for i in range(n):
        prod, comp = BASE + i * 10, BASE + i * 10 + 1
        bp_prod, bp_comp = BASE + i * 10 + 2, BASE + i * 10 + 3
        finished.append(prod)
        types += [_type(prod, f"Product {i}"), _type(comp, f"Component {i}"),
                  _type(bp_prod, f"Product {i} Blueprint"), _type(bp_comp, f"Component {i} Blueprint")]
        materials += [(bp_prod, 1, comp, 2), (bp_prod, 1, MIN_A, 5),
                      (bp_comp, 1, MIN_A, 10), (bp_comp, 1, MIN_B, 3)]
        products += [(bp_prod, 1, prod, quantity), (bp_comp, 1, comp, 1)]
        times += [(bp_prod, 1, 600.0), (bp_comp, 1, 300.0)]
    _empty_sde(types=types, blueprint_materials=materials, blueprint_products=products, blueprint_time=times)
    return finished


@pytest.fixture(autouse=True)
def _clean_sde(tenant):
    _empty_sde()
    yield
    _empty_sde()


@pytest.fixture
def queries(monkeypatch):
    """Counts every SQL statement storage sends (each goes through
    translate_placeholders); "sde" counts those that read an sde_* table."""
    counter = {"n": 0, "sde": 0}
    real = storage.translate_placeholders

    def counting(sql):
        counter["n"] += 1
        if "sde_" in sql:
            counter["sde"] += 1
        return real(sql)

    monkeypatch.setattr(storage, "translate_placeholders", counting)
    return counter


def _read_everything(product_ids):
    """The values every sidecar-backed getter returns for these products."""
    out = {}
    for pid in product_ids:
        bp = storage.get_blueprint_for_product(pid)
        entry = {"bp": bp, "cand": None}
        if bp is not None:
            entry["materials"] = storage.get_blueprint_materials(bp[0], bp[1])
            entry["time"] = storage.get_blueprint_time(bp[0], bp[1])
            entry["cand"] = storage.find_invention_recipe_candidates_by_product_type_id(bp[0])
        out[pid] = entry
    return out


def _all_ids(finished):
    ids = list(finished)
    for pid in finished:
        ids += [pid + 1, MIN_A, MIN_B]
    return ids


# -- rebuild / prefill ------------------------------------------------------


def test_prefetch_matches_single_lookups(tenant):
    finished = _seed_chain(6)
    ids = _all_ids(finished)
    storage.get_blueprint_for_product.cache_clear()
    storage.get_blueprint_materials.cache_clear()
    storage.get_blueprint_time.cache_clear()
    storage.find_invention_recipe_candidates_by_product_type_id.cache_clear()
    expected = _read_everything(ids)

    storage.get_blueprint_for_product.cache_clear()
    storage.get_blueprint_materials.cache_clear()
    storage.get_blueprint_time.cache_clear()
    storage.find_invention_recipe_candidates_by_product_type_id.cache_clear()
    storage.prefetch_recipes(ids)
    assert _read_everything(ids) == expected


def test_prefetch_query_budget_does_not_grow_with_item_count(tenant, queries):
    counts = {}
    for n in (3, 30):
        finished = _seed_chain(n)
        ids = _all_ids(finished)
        queries["n"] = 0
        storage.prefetch_recipes(ids)
        counts[n] = queries["n"]
        queries["n"] = 0
        _read_everything(ids)
        assert queries["n"] == 0, "everything the prefetch named must be served from the cache"
    # products, invention sources, materials, times, SDE type rows
    assert counts[3] == counts[30] <= 6


def test_prefetched_unknown_id_is_remembered_without_another_query(tenant, queries):
    _seed_chain(1)
    storage.prefetch_recipes([777777])
    queries["n"] = 0
    assert storage.get_blueprint_for_product(777777) is None
    assert queries["n"] == 0


def test_prefetch_still_finds_rows_added_later(tenant):
    finished = _seed_chain(2)
    storage.prefetch_recipes(finished)
    with storage.connect() as conn:
        conn.execute("INSERT INTO sde_types (type_id, group_id, type_name, volume, published, market_group_id, "
                     "meta_level) VALUES (96001, 1, 'Late Blueprint', 1.0, 1, NULL, NULL)")
        conn.execute("INSERT INTO sde_blueprint_products (blueprint_type_id, activity_id, product_type_id, quantity) "
                     "VALUES (96001, 1, 96002, 3.0)")
    assert storage.get_blueprint_for_product(96002) == (96001, 1, 3.0)


def test_complete_index_answers_unknown_ids_without_a_query(tenant, queries):
    finished = _seed_chain(4)
    queries["n"] = 0
    storage.load_recipe_index()
    assert queries["n"] == 4
    queries["n"] = 0
    assert storage.get_blueprint_for_product(123456789) is None
    assert storage.get_blueprint_materials(123456789, 1) == []
    assert storage.get_blueprint_time(123456789, 1) is None
    assert storage.find_invention_recipe_candidates_by_product_type_id(123456789) == ()
    assert storage.get_blueprint_materials(finished[0] + 2, 11) == []  # known blueprint, other activity
    assert queries["n"] == 0
    storage.load_recipe_index()  # already complete: no second read
    assert queries["n"] == 0


def test_index_load_is_serialised_across_threads(tenant, queries):
    _seed_chain(3)
    queries["n"] = 0
    errors = []

    def worker():
        try:
            with storage.tenant_context(tenant):
                storage.load_recipe_index()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert queries["n"] == 4  # one loader, five waiters that found it complete


def test_complete_index_equals_per_item_lookups(tenant):
    finished = _seed_chain(5)
    ids = _all_ids(finished)
    storage.get_blueprint_for_product.cache_clear()
    storage.get_blueprint_materials.cache_clear()
    storage.get_blueprint_time.cache_clear()
    storage.find_invention_recipe_candidates_by_product_type_id.cache_clear()
    expected = _read_everything(ids)

    storage.get_blueprint_for_product.cache_clear()
    storage.get_blueprint_materials.cache_clear()
    storage.get_blueprint_time.cache_clear()
    storage.find_invention_recipe_candidates_by_product_type_id.cache_clear()
    storage.load_recipe_index()
    assert _read_everything(ids) == expected


# -- published / tie-break behaviour ---------------------------------------


def _tie_break_sde():
    # 16672: unpublished leftover (lower id) next to the real published one.
    # 20000: only an unpublished blueprint. 20010: Manufacturing and Reaction
    # both exist. 20020: two published blueprints. 20030: quantity 0.
    types = [
        _type(45732, "Test Reaction Blueprint", published=0), _type(46207, "Tungsten Carbide Formula"),
        _type(50001, "Hidden Blueprint", published=0),
        _type(50011, "Manufacturing Blueprint"), _type(50012, "Reaction Formula"),
        _type(50021, "Blueprint B"), _type(50022, "Blueprint A"),
        _type(50031, "Broken Blueprint"),
    ]
    products = [
        (45732, 11, 16672, 20.0), (46207, 11, 16672, 10000.0),
        (50001, 1, 20000, 1.0),
        (50012, 11, 20010, 7.0), (50011, 1, 20010, 2.0),
        (50021, 1, 20020, 4.0), (50022, 1, 20020, 6.0),
        (50031, 1, 20030, 0.0),
    ]
    _empty_sde(types=types, blueprint_products=products)
    return [16672, 20000, 20010, 20020, 20030]


_TIE_EXPECTED = {
    16672: (46207, 11, 10000.0),
    20000: None,
    20010: (50011, 1, 2.0),
    20020: (50021, 1, 4.0),
    20030: (50031, 1, 1.0),
}


@pytest.mark.parametrize("mode", ["single", "prefetch", "index"])
def test_blueprint_for_product_tie_breaks_are_identical_in_every_path(tenant, mode):
    ids = _tie_break_sde()
    storage.get_blueprint_for_product.cache_clear()
    if mode == "prefetch":
        storage.prefetch_recipes(ids)
    elif mode == "index":
        storage.load_recipe_index()
    assert {pid: storage.get_blueprint_for_product(pid) for pid in ids} == _TIE_EXPECTED


# -- SDE apply --------------------------------------------------------------


def test_replace_sde_data_makes_new_values_visible_and_resets_the_index(tenant):
    finished = _seed_chain(1)
    prod = finished[0]
    bp_prod = prod + 2
    storage.load_recipe_index()
    assert storage.get_blueprint_for_product(prod) == (bp_prod, 1, 1.0)
    assert storage.get_blueprint_time(bp_prod, 1) == 600.0
    assert storage.get_product_quantity(bp_prod, 1, prod) == 1.0
    assert storage.get_blueprint_for_product(42424242) is None  # index says: no recipe

    # New snapshot: other blueprint, quantity, materials, time, and a recipe for an id that had none.
    _empty_sde(
        types=[_type(bp_prod + 100, "New Blueprint"), _type(42424243, "New Blueprint 2")],
        blueprint_products=[(bp_prod + 100, 1, prod, 9.0), (42424243, 1, 42424242, 2.0)],
        blueprint_materials=[(bp_prod + 100, 1, MIN_B, 11)],
        blueprint_time=[(bp_prod + 100, 1, 123.0)],
    )
    assert storage.get_blueprint_for_product(prod) == (bp_prod + 100, 1, 9.0)
    assert storage.get_blueprint_materials(bp_prod + 100, 1) == [(MIN_B, 11)]
    assert storage.get_blueprint_materials(bp_prod, 1) == []
    assert storage.get_blueprint_time(bp_prod + 100, 1) == 123.0
    assert storage.get_blueprint_time(bp_prod, 1) is None
    assert storage.get_product_quantity(bp_prod + 100, 1, prod) == 9.0
    assert storage.get_product_quantity(bp_prod, 1, prod) is None
    assert storage.get_blueprint_for_product(42424242) == (42424243, 1, 2.0)


def test_get_product_quantity_is_cached(tenant, queries):
    finished = _seed_chain(1)
    bp = finished[0] + 2
    assert storage.get_product_quantity(bp, 1, finished[0]) == 1.0
    queries["n"] = 0
    assert storage.get_product_quantity(bp, 1, finished[0]) == 1.0
    assert queries["n"] == 0


def test_warm_sde_caches_fills_recipes_and_type_rows(tenant, queries):
    finished = _seed_chain(5)
    storage.get_sde_type.cache_clear()
    summary = storage.warm_sde_caches()
    assert summary["recipes"] == 10 and summary["types"] > 0
    queries["n"] = 0
    for pid in finished:
        bp = storage.get_blueprint_for_product(pid)
        assert storage.get_sde_type(pid)[2].startswith("Product")
        assert storage.get_sde_type(bp[0]) is not None
        for material_id, _qty in storage.get_blueprint_materials(bp[0], bp[1]):
            assert storage.get_sde_type(material_id) is not None
        assert storage.get_blueprint_time(bp[0], bp[1]) == 600.0
    assert queries["n"] == 0


# -- planner ----------------------------------------------------------------


def test_closure_query_budget_is_flat_in_the_number_of_targets(tenant, queries):
    counts = {}
    for n in (5, 25):
        finished = _seed_chain(n)
        queries["n"] = 0
        closure = engine._structural_material_closure(finished)
        counts[n] = queries["n"]
        assert MIN_A in closure and MIN_B in closure and len(closure) >= 2 * n
    # three levels (finished, components, minerals), a few bulk reads each
    assert counts[5] == counts[25] <= 16


def _planner_queries(monkeypatch, tenant, n, queries, prefetch=True):
    from eve_trader.goonmetrics_client import CurrentPrice
    from . import special_order_fixtures as fx

    pg_helpers.wipe_tables("character_assets", "character_industry_jobs", "character_slots", "stock_targets")
    finished = _seed_chain(n)
    home = {m: CurrentPrice(type_id=m, updated="", buy=4.0, sell=5.0) for m in (MIN_A, MIN_B)}
    fx.patch_planner_network(monkeypatch, home=home, jita={})
    if not prefetch:
        monkeypatch.setattr(engine, "_prefetch_recipes", lambda type_ids: None)
    for pid in finished:
        storage.upsert_stock_target(pid, f"Product {pid}", backup_stock=3.0)
    cfg = fx.widget_cfg()
    queries["n"] = queries["sde"] = 0
    plan = engine.plan_production(cfg)
    assert {row.type_id for row in plan["build_list"]} >= set(finished)
    return queries["sde"]


def test_plan_production_cold_cache_sde_query_growth_per_target_is_small(monkeypatch, tenant, queries):
    small = _planner_queries(monkeypatch, tenant, 4, queries)
    large = _planner_queries(monkeypatch, tenant, 24, queries)
    per_target = (large - small) / 20
    # Without the recipe prefetch each target cost ~12 SDE reads (recipe, materials,
    # invention sources, type row, category, skills, ...). What is left are the
    # per-blueprint category and skill lookups, which are not part of the index.
    assert per_target <= 5, (small, large, per_target)

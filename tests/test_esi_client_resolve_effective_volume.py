from eve_trader import storage
import psycopg
import pytest

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

pytestmark = pg_helpers.postgres_required()
from eve_trader.esi_client import ESIClient, resolve_effective_volume, resolve_effective_volume_bulk


# GitHub issue #73: this used to be Production-only logic (production/
# engine.py's _haul_volume) - extracted here into esi_client.py so Trading's
# candidate_discovery could reuse the exact same lookup+cache behavior
# instead of the two tools drifting apart. These tests mirror
# test_production_engine.py's own _haul_volume tests one-for-one, since
# _haul_volume is now a thin wrapper around this function.

def test_resolve_effective_volume_returns_none_for_none_sde_volume():
    assert resolve_effective_volume(999999, None) is None


def test_resolve_effective_volume_uses_sde_volume_for_non_ship_non_module_categories():
    # category_id 4 = Material - never touches the packaged-volume cache/ESI
    # path at all (no monkeypatch of storage.get_cached_packaged_volume
    # here - a real call would error under the test DB).
    assert resolve_effective_volume(34, 0.01, category_id=4) == 0.01


def test_resolve_effective_volume_looks_up_category_when_not_passed(monkeypatch):
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 4)  # Material
    assert resolve_effective_volume(34, 0.01) == 0.01


def test_resolve_effective_volume_uses_packaged_volume_for_ships(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: 2500.0)
    assert resolve_effective_volume(587, 27289.0, category_id=6) == 2500.0  # SHIP_CATEGORY_ID


def test_resolve_effective_volume_uses_packaged_volume_for_capital_modules(monkeypatch):
    # Capital Shield Booster I: SDE flight volume 4000, real packaged_volume
    # 1000 (confirmed live via ESI) - the exact case GitHub issue #73 found
    # Trading's candidate_discovery was getting wrong.
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: 1000.0)
    assert resolve_effective_volume(20703, 4000.0, category_id=7) == 1000.0  # MODULE_CATEGORY_ID


def test_resolve_effective_volume_looks_up_esi_once_then_caches(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: None)  # not cached yet
    cached = {}
    monkeypatch.setattr(storage, "set_cached_packaged_volume", lambda type_id, v: cached.setdefault(type_id, v))
    monkeypatch.setattr(ESIClient, "get_packaged_volume", lambda self, type_id: 1000.0)

    assert resolve_effective_volume(20703, 4000.0, category_id=7) == 1000.0
    assert cached == {20703: 1000.0}


def test_resolve_effective_volume_falls_back_to_sde_volume_on_esi_failure_without_caching(monkeypatch):
    # A transient ESI hiccup should never get cached as if it were the real
    # packaged volume - that would permanently poison the cache.
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: None)
    cached = {}
    monkeypatch.setattr(storage, "set_cached_packaged_volume", lambda type_id, v: cached.setdefault(type_id, v))

    def _raise(self, type_id):
        raise RuntimeError("ESI down")
    monkeypatch.setattr(ESIClient, "get_packaged_volume", _raise)

    assert resolve_effective_volume(20703, 4000.0, category_id=7) == 4000.0
    assert cached == {}


# GitHub issue #96: the SDE-backed candidate universe used to call
# resolve_effective_volume once per Ship/Module-category type_id inside its
# own for-loop - on a deploy where type_packaged_volume wasn't fully
# backfilled yet, that meant hundreds of sequential live ESI calls in one
# request, timing out well before nginx's default 60s proxy_read_timeout.
# resolve_effective_volume_bulk resolves the whole batch concurrently.

def test_resolve_effective_volume_bulk_skips_esi_for_non_ship_non_module_categories():
    # category_id 4 = Material - never touches the packaged-volume cache/ESI
    # path (no monkeypatch of storage.get_cached_packaged_volume here - a
    # real call would error under the test DB).
    result = resolve_effective_volume_bulk([(34, 0.01, 4)])
    assert result == {34: 0.01}


def test_resolve_effective_volume_bulk_returns_none_for_none_sde_volume():
    result = resolve_effective_volume_bulk([(999999, None, None)])
    assert result == {999999: None}


def test_resolve_effective_volume_bulk_uses_already_cached_values_without_esi(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_packaged_volumes_bulk", lambda type_ids: {t: 1000.0 for t in type_ids})
    result = resolve_effective_volume_bulk([(20703, 4000.0, 7)])  # MODULE_CATEGORY_ID
    assert result == {20703: 1000.0}


def test_resolve_effective_volume_bulk_fetches_uncached_items_concurrently_and_caches_them(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_packaged_volumes_bulk", lambda type_ids: {})  # nothing cached yet
    cached = {}
    monkeypatch.setattr(storage, "set_cached_packaged_volume", lambda type_id, v: cached.setdefault(type_id, v))
    monkeypatch.setattr(ESIClient, "get_packaged_volume", lambda self, type_id: type_id * 10.0)

    result = resolve_effective_volume_bulk(
        [(1, 4000.0, 7), (2, 5000.0, 7), (3, 6000.0, 6)],  # 6 = SHIP_CATEGORY_ID
    )

    assert result == {1: 10.0, 2: 20.0, 3: 30.0}
    assert cached == {1: 10.0, 2: 20.0, 3: 30.0}


def test_resolve_effective_volume_bulk_falls_back_to_sde_volume_on_esi_failure_without_caching(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_packaged_volumes_bulk", lambda type_ids: {})
    cached = {}
    monkeypatch.setattr(storage, "set_cached_packaged_volume", lambda type_id, v: cached.setdefault(type_id, v))

    def _raise(self, type_id):
        raise RuntimeError("ESI down")
    monkeypatch.setattr(ESIClient, "get_packaged_volume", _raise)

    result = resolve_effective_volume_bulk([(20703, 4000.0, 7)])

    assert result == {20703: 4000.0}
    assert cached == {}


def test_resolve_effective_volume_bulk_propagates_ambient_tenant_to_worker_threads(monkeypatch):
    # Same bug class as region_order_stats_bulk's own test (GitHub issue
    # #58) - ThreadPoolExecutor worker threads don't inherit contextvars
    # from the submitting thread without storage.with_current_tenant.
    _tenant_id = "11111111-1111-1111-1111-111111111111"
    seen_tenants = []
    monkeypatch.setattr(storage, "get_cached_packaged_volumes_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "set_cached_packaged_volume", lambda type_id, v: None)

    def fake_get_packaged_volume(self, type_id):
        seen_tenants.append(storage.get_current_tenant())
        return 1000.0
    monkeypatch.setattr(ESIClient, "get_packaged_volume", fake_get_packaged_volume)

    with storage.tenant_context(_tenant_id):
        resolve_effective_volume_bulk([(20703, 4000.0, 7)])

    assert seen_tenants == [_tenant_id]


# Fix: the cache read is one bulk query, not one query per type.

@pytest.fixture
def _clean_volumes():
    def wipe():
        with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM type_packaged_volume WHERE type_id BETWEEN 100000 AND 100999")
    wipe()
    yield
    wipe()


def _count_queries(monkeypatch):
    import psycopg
    counter = {"n": 0}
    orig = psycopg.Cursor.execute

    def counting(self, query, params=None, **kw):
        counter["n"] += 1
        return orig(self, query, params, **kw)
    monkeypatch.setattr(psycopg.Cursor, "execute", counting)
    return counter


def _run_with_cache(n_types, cached_ids, monkeypatch):
    items = [(100000 + i, 4000.0 + i, 7) for i in range(n_types)]
    for tid in cached_ids:
        storage.set_cached_packaged_volume(100000 + tid, 1.5)
    monkeypatch.setattr(ESIClient, "get_packaged_volume", lambda self, type_id: 9.0)
    counter = _count_queries(monkeypatch)
    result = resolve_effective_volume_bulk(items)
    return result, counter["n"]


def test_resolve_effective_volume_bulk_query_count_does_not_grow_with_type_count(tenant, _clean_volumes, monkeypatch):
    _res_small, n_small = _run_with_cache(50, range(50), monkeypatch)
    _res_big, n_big = _run_with_cache(500, range(500), monkeypatch)
    assert n_big == n_small
    assert n_big < 10


def test_resolve_effective_volume_bulk_mixes_cached_and_missing_types(tenant, _clean_volumes, monkeypatch):
    result, _n = _run_with_cache(6, [0, 2, 4], monkeypatch)
    assert result[100000] == 1.5 and result[100002] == 1.5 and result[100004] == 1.5
    assert result[100001] == 9.0 and result[100003] == 9.0 and result[100005] == 9.0


def test_resolve_effective_volume_bulk_matches_single_path_and_handles_duplicates(tenant, _clean_volumes, monkeypatch):
    storage.set_cached_packaged_volume(100000, 2.5)
    monkeypatch.setattr(ESIClient, "get_packaged_volume", lambda self, type_id: 7.0)
    items = [(100000, 4000.0, 7), (100001, 5000.0, 6), (100000, 4000.0, 7), (34, 0.01, 4), (5, None, None)]
    bulk = resolve_effective_volume_bulk(items)
    single = {t: resolve_effective_volume(t, v, category_id=c) for t, v, c in items}
    assert bulk == single == {100000: 2.5, 100001: 7.0, 34: 0.01, 5: None}


def test_resolve_effective_volume_bulk_prefetches_missing_categories_once(tenant, monkeypatch):
    calls = []
    orig = storage.prefetch_type_categories
    monkeypatch.setattr(storage, "prefetch_type_categories", lambda ids: calls.append(list(ids)) or orig(ids))
    resolve_effective_volume_bulk([(34, 0.01, None), (35, 0.01, None), (36, 0.01, 4)])
    assert len(calls) == 1 and sorted(calls[0]) == [34, 35]

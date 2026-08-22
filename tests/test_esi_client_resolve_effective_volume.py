from eve_trader import storage
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
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: 1000.0)
    result = resolve_effective_volume_bulk([(20703, 4000.0, 7)])  # MODULE_CATEGORY_ID
    assert result == {20703: 1000.0}


def test_resolve_effective_volume_bulk_fetches_uncached_items_concurrently_and_caches_them(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: None)  # nothing cached yet
    cached = {}
    monkeypatch.setattr(storage, "set_cached_packaged_volume", lambda type_id, v: cached.setdefault(type_id, v))
    monkeypatch.setattr(ESIClient, "get_packaged_volume", lambda self, type_id: type_id * 10.0)

    result = resolve_effective_volume_bulk(
        [(1, 4000.0, 7), (2, 5000.0, 7), (3, 6000.0, 6)],  # 6 = SHIP_CATEGORY_ID
    )

    assert result == {1: 10.0, 2: 20.0, 3: 30.0}
    assert cached == {1: 10.0, 2: 20.0, 3: 30.0}


def test_resolve_effective_volume_bulk_falls_back_to_sde_volume_on_esi_failure_without_caching(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: None)
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
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda type_id: None)
    monkeypatch.setattr(storage, "set_cached_packaged_volume", lambda type_id, v: None)

    def fake_get_packaged_volume(self, type_id):
        seen_tenants.append(storage.get_current_tenant())
        return 1000.0
    monkeypatch.setattr(ESIClient, "get_packaged_volume", fake_get_packaged_volume)

    with storage.tenant_context(_tenant_id):
        resolve_effective_volume_bulk([(20703, 4000.0, 7)])

    assert seen_tenants == [_tenant_id]

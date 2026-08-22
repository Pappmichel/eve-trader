from eve_trader import storage
from eve_trader.esi_client import ESIClient, resolve_effective_volume


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

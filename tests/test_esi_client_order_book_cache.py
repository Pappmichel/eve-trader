"""GitHub issue #103: region_order_stats/structure_orders_raw are cached
class-wide for a short TTL. Region books are keyed by (region_id, type_id);
structure books are keyed by (structure_id, auth_role) — the ESI principal,
not tenant_id (F-02).
"""
import pytest

from eve_trader.esi_client import ESIClient, OrderStats


@pytest.fixture(autouse=True)
def _reset_order_book_caches():
    # Class-wide, not per-instance (see esi_client.py's _ORDER_BOOK_CACHE_TTL
    # comment) - a test that populates it would otherwise leak into whichever
    # test runs next, in this file or another.
    ESIClient.clear_order_book_caches()
    yield
    ESIClient.clear_order_book_caches()


def _fake_get_all_pages(monkeypatch, responses):
    """responses: {path: return_value} - counts calls per path."""
    calls: dict[str, int] = {}

    def _get_all_pages(self, path, params=None, auth_role=None, max_workers=5):
        calls[path] = calls.get(path, 0) + 1
        return responses[path]
    monkeypatch.setattr(ESIClient, "_get_all_pages", _get_all_pages)
    return calls


def test_region_order_stats_caches_across_fresh_client_instances(monkeypatch):
    calls = _fake_get_all_pages(monkeypatch, {
        "/markets/10000002/orders/": [
            {"is_buy_order": False, "price": 5.5, "volume_remain": 1000},
        ],
    })

    first = ESIClient().region_order_stats(10000002, 34)
    second = ESIClient().region_order_stats(10000002, 34)

    assert calls["/markets/10000002/orders/"] == 1
    assert first == second


def test_region_order_stats_cache_is_keyed_by_region_and_type_id(monkeypatch):
    calls = _fake_get_all_pages(monkeypatch, {
        "/markets/10000002/orders/": [{"is_buy_order": False, "price": 5.5, "volume_remain": 1000}],
        "/markets/10000043/orders/": [{"is_buy_order": False, "price": 9.0, "volume_remain": 500}],
    })

    ESIClient().region_order_stats(10000002, 34)
    ESIClient().region_order_stats(10000043, 34)  # different region
    ESIClient().region_order_stats(10000002, 35)  # different type_id, same region

    assert calls["/markets/10000002/orders/"] == 2
    assert calls["/markets/10000043/orders/"] == 1


def test_region_order_stats_refetches_after_clear_order_book_caches(monkeypatch):
    calls = _fake_get_all_pages(monkeypatch, {
        "/markets/10000002/orders/": [{"is_buy_order": False, "price": 5.5, "volume_remain": 1000}],
    })

    ESIClient().region_order_stats(10000002, 34)
    ESIClient.clear_order_book_caches()
    ESIClient().region_order_stats(10000002, 34)

    assert calls["/markets/10000002/orders/"] == 2


def test_structure_orders_raw_caches_across_fresh_client_instances(monkeypatch):
    calls = _fake_get_all_pages(monkeypatch, {
        "/markets/structures/1234567890/": [{"type_id": 34, "is_buy_order": False, "price": 5.5, "volume_remain": 1000}],
    })

    first = ESIClient().structure_orders_raw(1234567890, "seller")
    second = ESIClient().structure_orders_raw(1234567890, "seller")

    assert calls["/markets/structures/1234567890/"] == 1
    assert first == second


def test_structure_orders_raw_returns_an_independent_copy_each_call(monkeypatch):
    """A caller mutating its own returned list (e.g. filtering in place) must
    never corrupt the shared cached value for the next caller."""
    _fake_get_all_pages(monkeypatch, {
        "/markets/structures/1234567890/": [{"type_id": 34, "is_buy_order": False, "price": 5.5, "volume_remain": 1000}],
    })

    first = ESIClient().structure_orders_raw(1234567890, "seller")
    first.clear()
    second = ESIClient().structure_orders_raw(1234567890, "seller")

    assert len(second) == 1


def test_structure_order_stats_bulk_downloads_the_book_once_across_calls(monkeypatch):
    """The whole point of #103 for structures: structure_order_stats_bulk and
    check_undercut both call structure_orders_raw independently - back-to-back
    calls (same structure_id) must share one download, not two."""
    calls = _fake_get_all_pages(monkeypatch, {
        "/markets/structures/1234567890/": [
            {"type_id": 34, "is_buy_order": False, "price": 5.5, "volume_remain": 1000},
            {"type_id": 35, "is_buy_order": False, "price": 9.0, "volume_remain": 500},
        ],
    })

    client = ESIClient()
    first = client.structure_order_stats_bulk(1234567890, [34, 35], auth_role="seller")
    second = client.structure_order_stats_bulk(1234567890, [34, 35], auth_role="seller")

    assert calls["/markets/structures/1234567890/"] == 1
    assert first[34] == second[34] == OrderStats(sell_percentile=5.5, sell_volume=1000, buy_percentile=None, buy_volume=0.0)


def test_structure_orders_raw_does_not_reuse_cache_across_auth_roles(monkeypatch):
    """F-02: cache key is (structure_id, auth_role), not tenant_id.
    Two characters (same or different tenant) with different ESI principals
    must not share a book or a 403."""
    calls: dict[str, list[str]] = {}

    def _get_all_pages(self, path, params=None, auth_role=None, max_workers=5):
        calls.setdefault(path, []).append(auth_role)
        return [{"type_id": 34, "is_buy_order": False, "price": 5.5, "volume_remain": 1000, "auth": auth_role}]

    monkeypatch.setattr(ESIClient, "_get_all_pages", _get_all_pages)

    a = ESIClient().structure_orders_raw(1234567890, "seller:1")
    b = ESIClient().structure_orders_raw(1234567890, "seller:2")

    assert calls["/markets/structures/1234567890/"] == ["seller:1", "seller:2"]
    assert a[0]["auth"] == "seller:1"
    assert b[0]["auth"] == "seller:2"


def test_structure_orders_raw_same_principal_hits_cache_even_across_clients(monkeypatch):
    calls = _fake_get_all_pages(monkeypatch, {
        "/markets/structures/1234567890/": [{"type_id": 34, "is_buy_order": False, "price": 5.5, "volume_remain": 1000}],
    })
    ESIClient().structure_orders_raw(1234567890, "seller:2112625428")
    ESIClient().structure_orders_raw(1234567890, "seller:2112625428")
    assert calls["/markets/structures/1234567890/"] == 1


def test_unauthorized_principal_does_not_receive_another_principals_cached_book(monkeypatch):
    from eve_trader.esi_client import ESIError

    def _get_all_pages(self, path, params=None, auth_role=None, max_workers=5):
        if auth_role == "seller:1":
            return [{"type_id": 34, "price": 1}]
        raise ESIError("forbidden")

    monkeypatch.setattr(ESIClient, "_get_all_pages", _get_all_pages)
    cached = ESIClient().structure_orders_raw(99, "seller:1")
    assert cached[0]["type_id"] == 34
    with pytest.raises(ESIError):
        ESIClient().structure_orders_raw(99, "seller:2")


def test_structure_book_cache_expires(monkeypatch):
    calls = _fake_get_all_pages(monkeypatch, {
        "/markets/structures/1/": [{"type_id": 34}],
    })
    now = {"t": 1_000.0}
    monkeypatch.setattr("eve_trader.esi_client.time.time", lambda: now["t"])
    ESIClient().structure_orders_raw(1, "seller:1")
    now["t"] += 31  # TTL is 30s
    ESIClient().structure_orders_raw(1, "seller:1")
    assert calls["/markets/structures/1/"] == 2


def test_structure_book_cache_concurrent_same_principal_fetches_once(monkeypatch):
    import time as real_time
    from concurrent.futures import ThreadPoolExecutor

    calls = {"n": 0}

    def _get_all_pages(self, path, params=None, auth_role=None, max_workers=5):
        calls["n"] += 1
        real_time.sleep(0.02)
        return [{"type_id": 34}]

    monkeypatch.setattr(ESIClient, "_get_all_pages", _get_all_pages)

    def _one(_i):
        return ESIClient().structure_orders_raw(7, "seller:1")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_one, range(8)))

    assert calls["n"] == 1
    assert all(r == [{"type_id": 34}] for r in results)


def test_structure_book_cache_concurrent_different_principals_do_not_share(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    def _get_all_pages(self, path, params=None, auth_role=None, max_workers=5):
        return [{"auth": auth_role}]

    monkeypatch.setattr(ESIClient, "_get_all_pages", _get_all_pages)

    def _one(role):
        return ESIClient().structure_orders_raw(7, role)[0]["auth"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        roles = [f"seller:{i}" for i in range(8)]
        got = list(pool.map(_one, roles))

    assert got == roles

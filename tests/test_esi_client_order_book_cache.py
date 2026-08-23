"""GitHub issue #103: region_order_stats/structure_orders_raw are cached
class-wide for a short TTL, keyed by (region_id, type_id)/structure_id - same
pattern/reasoning as test_esi_client_price_cache.py's own caches, just a
shorter TTL (order books move faster than a market-wide price snapshot).
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

from eve_trader import storage
from eve_trader.esi_client import ESIClient, OrderStats

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def test_region_order_stats_bulk_propagates_ambient_tenant_to_worker_threads(monkeypatch):
    # GitHub issue #58 (found in a full-codebase audit 2026-08-21):
    # ThreadPoolExecutor worker threads don't inherit contextvars from the
    # thread that submitted the work - region_order_stats_bulk must wrap
    # each submitted call in storage.with_current_tenant so anything on that
    # path that transitively touches storage.py still sees the real ambient
    # tenant, not none at all. Without the fix, storage.get_current_tenant()
    # inside the worker thread returns None regardless of what's set on the
    # calling thread.
    seen_tenants = []

    def fake_region_order_stats(self, region_id, type_id):
        seen_tenants.append(storage.get_current_tenant())
        return OrderStats(None, 0.0, None, 0.0)
    monkeypatch.setattr(ESIClient, "region_order_stats", fake_region_order_stats)

    with storage.tenant_context(_TENANT_ID):
        ESIClient().region_order_stats_bulk(10000002, [1, 2, 3])

    assert seen_tenants == [_TENANT_ID, _TENANT_ID, _TENANT_ID]


def test_region_order_stats_bulk_with_no_ambient_tenant_stays_none(monkeypatch):
    # The flip side - no ambient tenant on the calling thread means no
    # ambient tenant on the worker thread either, not a stale leftover from
    # a previous call (with_current_tenant captures fresh each call).
    seen_tenants = []

    def fake_region_order_stats(self, region_id, type_id):
        seen_tenants.append(storage.get_current_tenant())
        return OrderStats(None, 0.0, None, 0.0)
    monkeypatch.setattr(ESIClient, "region_order_stats", fake_region_order_stats)

    ESIClient().region_order_stats_bulk(10000002, [1])

    assert seen_tenants == [None]


def test_region_order_stats_bulk_caps_in_flight_even_for_thousands_of_ids(monkeypatch):
    """Cleanup now feeds region_order_stats_bulk in shortlist_refresh_batch_size
    waves, but even a single 2000-id call must not spawn 2000 concurrent ESI
    requests - that would burn ESI's 420 error-limit (and the separate 429
    burst limiter, GitHub issue #99). max_workers is the in-flight cap;
    _error_limit_remain backoff stays as-is (already correct)."""
    import threading
    import time

    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()
    remain_samples = []

    def fake_region_order_stats(self, region_id, type_id):
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            remain_samples.append(ESIClient._error_limit_remain)
        time.sleep(0.002)
        with lock:
            in_flight -= 1
        return OrderStats(None, 0.0, None, 0.0)
    monkeypatch.setattr(ESIClient, "region_order_stats", fake_region_order_stats)
    ESIClient._error_limit_remain = 100

    ESIClient().region_order_stats_bulk(10000002, list(range(200)), max_workers=10)

    assert max_in_flight <= 10
    assert min(remain_samples) == 100  # successful mock calls never decrement the error budget

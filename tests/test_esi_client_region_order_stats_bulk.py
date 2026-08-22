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

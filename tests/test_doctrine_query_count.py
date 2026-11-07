"""Query counts of the Doctrine status / shopping-list paths against the real
test database: the number of storage queries must not grow with the number of
fittings (fitting items are read in bulk, not once per fitting)."""
import pytest

from eve_trader import storage
from eve_trader.doctrine import engine
from eve_trader.doctrine.config import DoctrineConfig
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production import esi_sync as production_esi_sync

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401

pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

HULL = 900001
MODULES = [910000 + i for i in range(8)]


@pytest.fixture
def query_counter(monkeypatch):
    """Counts every statement run through storage's connection wrappers."""
    counter = {"n": 0}
    real_conn_execute = storage._TranslatingConnection.execute
    real_conn_executemany = storage._TranslatingConnection.executemany
    real_cur_execute = storage._TranslatingCursor.execute

    def conn_execute(self, sql, params=None):
        counter["n"] += 1
        return real_conn_execute(self, sql, params)

    def conn_executemany(self, sql, params_seq):
        counter["n"] += 1
        return real_conn_executemany(self, sql, params_seq)

    def cur_execute(self, sql, params=None):
        counter["n"] += 1
        return real_cur_execute(self, sql, params)

    monkeypatch.setattr(storage._TranslatingConnection, "execute", conn_execute)
    monkeypatch.setattr(storage._TranslatingConnection, "executemany", conn_executemany)
    monkeypatch.setattr(storage._TranslatingCursor, "execute", cur_execute)
    return counter


def _seed(n_fittings: int) -> str:
    doctrine_id = storage.create_doctrine(f"Doctrine {n_fittings}", None)
    for i in range(n_fittings):
        fitting_id = storage.create_fitting(doctrine_id, f"Fit {i}", HULL, "[Hull, Fit]\n", None, 1, 2, None)
        storage.replace_fitting_items(
            fitting_id, [(line, "low", type_id, 1, False) for line, type_id in enumerate(MODULES, start=1)])
    return doctrine_id


def _stub_external(monkeypatch):
    monkeypatch.setattr(engine, "read_esi", lambda *a, **k: [{"type_id": MODULES[0]}])
    monkeypatch.setattr(storage, "esi_stock_from_asset_rows",
                        lambda rows, type_ids, location_id, **k: {t: 0.0 for t in type_ids})
    monkeypatch.setattr(
        engine.production_pricing, "home_prices",
        lambda cfg, type_ids: {t: CurrentPrice(type_id=t, updated="2026-01-01", buy=9.0, sell=10.0)
                               for t in type_ids})


def _doctrine_status_queries(n_fittings: int, query_counter) -> int:
    doctrine_id = _seed(n_fittings)
    row = storage.get_doctrine(doctrine_id)
    before = query_counter["n"]
    status = engine.doctrine_status(row, DoctrineConfig())
    assert len(status.fittings) == n_fittings
    return query_counter["n"] - before


def test_doctrine_status_query_count_does_not_grow_with_fittings(tenant, monkeypatch, query_counter):
    _stub_external(monkeypatch)
    small = _doctrine_status_queries(5, query_counter)
    large = _doctrine_status_queries(50, query_counter)
    # Everything left per fitting is a constant handful (freshness, hull name);
    # the item loads were 2-4 queries per fitting before the bulk read.
    assert large - small <= 2 * (50 - 5), (small, large)


def test_doctrine_status_reads_fitting_items_once(tenant, monkeypatch):
    _stub_external(monkeypatch)
    _seed(12)
    calls = []
    real_bulk = storage.load_fitting_items_bulk
    monkeypatch.setattr(storage, "load_fitting_items_bulk", lambda ids: calls.append(len(ids)) or real_bulk(ids))
    monkeypatch.setattr(storage, "load_fitting_items",
                        lambda fid: pytest.fail("per-fitting item load on the status path"))

    status = engine.doctrine_status(storage.list_doctrines()[0], DoctrineConfig())

    assert len(status.fittings) == 12
    assert calls == [12]


def test_stockpile_rows_query_count_does_not_grow_with_fittings(tenant, monkeypatch, query_counter):
    _stub_external(monkeypatch)
    _seed(5)
    engine.stockpile_rows_for_doctrine(cfg=DoctrineConfig())  # warm module-level caches
    before = query_counter["n"]
    rows_small, _ = engine.stockpile_rows_for_doctrine(cfg=DoctrineConfig())
    small = query_counter["n"] - before
    _seed(45)  # 50 in total
    before = query_counter["n"]
    rows_large, _ = engine.stockpile_rows_for_doctrine(cfg=DoctrineConfig())
    large = query_counter["n"] - before
    assert len(rows_small) == 5 * (len(MODULES) + 1)
    assert len(rows_large) == 50 * (len(MODULES) + 1)
    assert large == small, (small, large)


class _FakeESIClient:
    def __init__(self, *a, **k):
        pass

    def region_order_stats_bulk(self, region_id, type_ids):
        return {}

    def structure_order_stats_bulk(self, structure_id, type_ids, auth_role):
        return {}


def _stub_shopping(monkeypatch):
    _stub_external(monkeypatch)

    class _FakePlanContext:
        def __init__(self, cfg, extra_type_ids=(), **kwargs):
            self.home = {t: CurrentPrice(type_id=t, updated="2026-01-01", buy=9.0, sell=10.0)
                         for t in extra_type_ids}
            self.jita = dict(self.home)
            self.cost_indices = {}
            self.adjusted_prices = {}
            self.selected_decryptors = {}

    monkeypatch.setattr(engine, "_PlanContext", _FakePlanContext)
    monkeypatch.setattr(engine, "unit_cost_detail", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(engine, "_haul_volume", lambda type_id, cfg: 1.0)
    monkeypatch.setattr(engine, "ESIClient", _FakeESIClient)
    monkeypatch.setattr(production_esi_sync, "list_capability_characters", lambda *a, **k: [])


def test_shopping_list_query_count_does_not_grow_with_fittings(tenant, monkeypatch, query_counter):
    _stub_shopping(monkeypatch)
    _seed(5)
    engine.shopping_list(cfg=DoctrineConfig())  # warm module-level caches
    before = query_counter["n"]
    rows_small, _ = engine.shopping_list(cfg=DoctrineConfig())
    small = query_counter["n"] - before
    _seed(45)
    before = query_counter["n"]
    rows_large, _ = engine.shopping_list(cfg=DoctrineConfig())
    large = query_counter["n"] - before
    assert len(rows_small) == len(rows_large) == len(MODULES) + 1
    assert [r.shortfall for r in rows_large] == [r.shortfall * 10 for r in rows_small]
    assert large == small, (small, large)


def test_load_fitting_items_bulk_matches_single_loads(tenant):
    doctrine_id = storage.create_doctrine("Bulk", None)
    a = storage.create_fitting(doctrine_id, "A", HULL, "", None, 1, 1, None)
    b = storage.create_fitting(doctrine_id, "B", HULL, "", None, 1, 1, None)
    empty = storage.create_fitting(doctrine_id, "Empty", HULL, "", None, 1, 1, None)
    storage.replace_fitting_items(a, [(2, "high", 11, 1, False), (1, "low", 12, 3, True), (1, "charge", 13, 2, False)])
    storage.replace_fitting_items(b, [(1, "low", 14, 1, False)])

    bulk = storage.load_fitting_items_bulk([a, b, empty, a])

    assert bulk == {a: storage.load_fitting_items(a), b: storage.load_fitting_items(b), empty: []}
    assert storage.load_fitting_items_bulk([]) == {}

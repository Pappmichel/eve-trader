import pytest

from eve_trader import storage
from eve_trader.goonmetrics_client import HistoryPoint

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401
# tenant_pair fixture comes from conftest.py (registered project-wide)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def _point(type_id: int, region_id: int = 10000002) -> HistoryPoint:
    return HistoryPoint(region_id=region_id, type_id=type_id, date="2026-08-01",
                         min_price=1.0, max_price=2.0, avg_price=1.5, movement=100.0, num_orders=5)


def test_read_goonmetrics_history_for_types_filters_to_requested_ids(tenant):
    # Confirmed real gap (Finding 3.5): do_shortlist_trends used to pull the
    # *entire* goonmetrics_history table (every candidate ever price-checked,
    # not just the shortlist's own items) on every Shortlist page load.
    storage.save_goonmetrics_history([_point(1), _point(2), _point(3)])

    df = storage.read_goonmetrics_history_for_types([1, 3])

    assert sorted(df["type_id"].tolist()) == [1, 3]


def test_read_goonmetrics_history_for_types_filters_regions_and_dates(tenant):
    # Shortlist trends only use two regions and the last 30 days; loading
    # every stored day was ~330k rows on a large shortlist (2026-10-03).
    def _at(type_id, region_id, date):
        return HistoryPoint(region_id=region_id, type_id=type_id, date=date, min_price=1.0,
                            max_price=2.0, avg_price=1.5, movement=100.0, num_orders=5)
    storage.save_goonmetrics_history([
        _at(1, 10000002, "2026-09-01"), _at(1, 10000002, "2026-09-20"),
        _at(1, 10000009, "2026-09-20"), _at(1, 10000043, "2026-09-20"),
    ])

    df = storage.read_goonmetrics_history_for_types(
        [1], region_ids=[10000002, 10000009], after_date="2026-09-01")

    assert sorted(zip(df["region_id"], df["date"])) == [
        (10000002, "2026-09-20"), (10000009, "2026-09-20")]


def test_read_goonmetrics_history_for_types_empty_list_returns_empty_df(tenant):
    storage.save_goonmetrics_history([_point(1)])

    df = storage.read_goonmetrics_history_for_types([])

    assert df.empty


def test_goonmetrics_history_type_ids_for_tenant_excludes_another_tenants_items(tenant_pair):
    """T3-03 (business-logic audit follow-up, 2026-09-26): goonmetrics_
    history itself is a global, shared-across-every-tenant cache (the price
    DATA is legitimately public/shared) - but the *listing* of which
    type_ids exist in it must be scoped to this tenant's own shortlist/
    candidate_universe items, or it leaks which items another tenant is
    researching."""
    tenant_a, tenant_b = tenant_pair
    # goonmetrics_history has no tenant_id column at all (a genuinely global
    # table), but save_goonmetrics_history still goes through the normal
    # tenant-scoped connect() - needs *some* ambient tenant set to open a
    # connection at all, even though it won't filter by it for this table.
    with storage.tenant_context(tenant_a):
        storage.save_goonmetrics_history([_point(1), _point(2), _point(3)])  # shared cache, both tenants can see the data

    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute("INSERT INTO shortlist (item_id, item) VALUES (?, ?)", (1, "Tritanium"))
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute("INSERT INTO candidate_universe (type_id) VALUES (?)", (2,))
        # type_id 3 is in neither tenant's own items - present in the shared
        # cache, but neither tenant should ever see it listed.

    with storage.tenant_context(tenant_a):
        assert storage.goonmetrics_history_type_ids_for_tenant() == [1]
    with storage.tenant_context(tenant_b):
        assert storage.goonmetrics_history_type_ids_for_tenant() == [2]


def test_goonmetrics_history_type_ids_for_tenant_covers_both_shortlist_and_candidate_universe(tenant):
    storage.save_goonmetrics_history([_point(1), _point(2)])
    with storage.connect() as conn:
        conn.execute("INSERT INTO shortlist (item_id, item) VALUES (?, ?)", (1, "Tritanium"))
        conn.execute("INSERT INTO candidate_universe (type_id) VALUES (?)", (2,))

    assert sorted(storage.goonmetrics_history_type_ids_for_tenant()) == [1, 2]


def test_read_goonmetrics_avg_prices_since_filters_region_ids_and_date(tenant):
    # goonmetrics_history is global and not reset between tests: use ids no
    # other test touches and clean up afterwards.
    a, b, c, d = 900001, 900002, 900003, 900004

    def pt(type_id, date, avg, region_id=10000002):
        return HistoryPoint(region_id=region_id, type_id=type_id, date=date, min_price=1.0, max_price=2.0,
                            avg_price=avg, movement=1.0, num_orders=1)

    storage.save_goonmetrics_history([
        pt(a, "2026-09-01", 1.0), pt(a, "2026-09-20", 2.0), pt(a, "2026-09-21", 3.0),
        pt(b, "2026-09-25", 5.0), pt(c, "2026-09-25", 9.0),
        pt(a, "2026-09-25", 7.0, region_id=10000009),
    ])
    try:
        out = storage.read_goonmetrics_avg_prices_since(10000002, [a, b, d], "2026-09-20")
        assert out == {a: [("2026-09-20", 2.0), ("2026-09-21", 3.0)], b: [("2026-09-25", 5.0)]}
        assert storage.read_goonmetrics_avg_prices_since(10000002, [], "2026-09-20") == {}
    finally:
        with storage.connect() as conn:
            conn.execute("DELETE FROM goonmetrics_history WHERE type_id IN (?,?,?,?)", (a, b, c, d))

import pytest

from eve_trader import storage
from eve_trader.goonmetrics_client import HistoryPoint

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

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


def test_read_goonmetrics_history_for_types_empty_list_returns_empty_df(tenant):
    storage.save_goonmetrics_history([_point(1)])

    df = storage.read_goonmetrics_history_for_types([])

    assert df.empty

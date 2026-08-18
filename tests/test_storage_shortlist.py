import pytest

from eve_trader import storage
from eve_trader.models import ShortlistItem

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def test_deactivate_shortlist_items(tenant):
    storage.upsert_shortlist([
        ShortlistItem(item="Keep Me", item_id=1, category="Material", volume_m3=1.0, active=True),
        ShortlistItem(item="Drop Me", item_id=2, category="Material", volume_m3=1.0, active=True),
    ])

    storage.deactivate_shortlist_items([2])

    items = {i.item_id: i for i in storage.load_shortlist()}
    assert items[1].active is True
    assert items[2].active is False


def test_deactivate_shortlist_items_noop_on_empty_list(tenant):
    storage.upsert_shortlist([
        ShortlistItem(item="Keep Me", item_id=1, category="Material", volume_m3=1.0, active=True),
    ])

    storage.deactivate_shortlist_items([])

    assert storage.load_shortlist()[0].active is True


def test_skip_streak_start_get_clear(tenant):
    storage.start_shortlist_skip_streak([1, 2], "2026-06-01T00:00:00")

    assert storage.get_shortlist_skip_since() == {
        1: "2026-06-01T00:00:00", 2: "2026-06-01T00:00:00",
    }

    storage.clear_shortlist_skip_streak([1])
    assert storage.get_shortlist_skip_since() == {2: "2026-06-01T00:00:00"}


def test_skip_streak_start_does_not_overwrite_existing_streak(tenant):
    storage.start_shortlist_skip_streak([1], "2026-06-01T00:00:00")
    # A later refresh's Skip evaluation must not push the streak start out.
    storage.start_shortlist_skip_streak([1], "2026-06-15T00:00:00")

    assert storage.get_shortlist_skip_since() == {1: "2026-06-01T00:00:00"}

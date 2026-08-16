from eve_trader import storage
from eve_trader.models import ShortlistItem


def test_deactivate_shortlist_items(tmp_path):
    db_path = tmp_path / "test.db"
    storage.upsert_shortlist([
        ShortlistItem(item="Keep Me", item_id=1, category="Material", volume_m3=1.0, active=True),
        ShortlistItem(item="Drop Me", item_id=2, category="Material", volume_m3=1.0, active=True),
    ], db_path=db_path)

    storage.deactivate_shortlist_items([2], db_path=db_path)

    items = {i.item_id: i for i in storage.load_shortlist(db_path=db_path)}
    assert items[1].active is True
    assert items[2].active is False


def test_deactivate_shortlist_items_noop_on_empty_list(tmp_path):
    db_path = tmp_path / "test.db"
    storage.upsert_shortlist([
        ShortlistItem(item="Keep Me", item_id=1, category="Material", volume_m3=1.0, active=True),
    ], db_path=db_path)

    storage.deactivate_shortlist_items([], db_path=db_path)

    assert storage.load_shortlist(db_path=db_path)[0].active is True


def test_skip_streak_start_get_clear(tmp_path):
    db_path = tmp_path / "test.db"
    storage.start_shortlist_skip_streak([1, 2], "2026-06-01T00:00:00", db_path=db_path)

    assert storage.get_shortlist_skip_since(db_path=db_path) == {
        1: "2026-06-01T00:00:00", 2: "2026-06-01T00:00:00",
    }

    storage.clear_shortlist_skip_streak([1], db_path=db_path)
    assert storage.get_shortlist_skip_since(db_path=db_path) == {2: "2026-06-01T00:00:00"}


def test_skip_streak_start_does_not_overwrite_existing_streak(tmp_path):
    db_path = tmp_path / "test.db"
    storage.start_shortlist_skip_streak([1], "2026-06-01T00:00:00", db_path=db_path)
    # A later refresh's Skip evaluation must not push the streak start out.
    storage.start_shortlist_skip_streak([1], "2026-06-15T00:00:00", db_path=db_path)

    assert storage.get_shortlist_skip_since(db_path=db_path) == {1: "2026-06-01T00:00:00"}

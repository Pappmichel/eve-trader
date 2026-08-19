import pandas as pd

from eve_trader import actions, storage


def test_do_add_to_shortlist_recomputes_category_fresh_from_sde(monkeypatch):
    # Confirmed real bug: category was copied straight from new_candidates.
    # category, which is only as fresh as the last candidate_universe/
    # focused_candidates rebuild - after a categorization logic fix, every
    # item added *before* the next rebuild kept showing the old, wrong label
    # (e.g. skills as "Material") even though the fix was already live. Fixed
    # by re-deriving the category from the SDE at add-time instead of
    # trusting the cached value.
    new_candidates_df = pd.DataFrame([
        {"run_ts": "2026-01-01T00:00:00", "item": "Amarr Frigate", "category": "Material",
         "type_id": 3331, "volume_m3": 0.0, "paired_days": 5, "profitable_days": 5,
         "hit_rate": 1.0, "latest_margin": 0.1, "best_margin": 0.2, "avg_profit_m3": 0.0,
         "avg_sell_movement": 1.0, "score": 1.0, "recommendation": "Consider import",
         "add_flag": 1, "meta_level": None},
    ])
    monkeypatch.setattr(storage, "read_table", lambda table: new_candidates_df)
    monkeypatch.setattr(storage, "load_sde_category_names", lambda: {16: "Skill"})
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 16)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 273, "Amarr Frigate", 0.0, 1, 1, 0, None))

    captured = {}
    monkeypatch.setattr(storage, "upsert_shortlist", lambda items: captured.setdefault("items", items))

    result = actions.do_add_to_shortlist()

    assert result == {"added": 1}
    assert captured["items"][0].category == "Skill"


def test_do_recategorize_shortlist_fixes_stale_booster_labels(monkeypatch):
    from eve_trader.models import ShortlistItem

    existing = [
        ShortlistItem(item="Blue Pill", item_id=44, category="Implant", volume_m3=0.1, active=True),
        ShortlistItem(item="Already Correct", item_id=45, category="Skill", volume_m3=0.1, active=True),
    ]
    monkeypatch.setattr(storage, "load_shortlist", lambda: existing)
    monkeypatch.setattr(storage, "load_sde_category_names", lambda: {20: "Implant", 16: "Skill"})
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: {44: 20, 45: 16}[type_id])
    # group_id 746 = Booster for item 44 (currently mislabeled "Implant")
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, {44: 746, 45: 273}[type_id], "x", 0.1, 1, 1, 0, None))

    captured = {}
    monkeypatch.setattr(storage, "upsert_shortlist", lambda items: captured.setdefault("items", items))

    result = actions.do_recategorize_shortlist()

    assert result == {"checked": 2, "recategorized": 1}
    by_id = {i.item_id: i for i in captured["items"]}
    assert by_id[44].category == "Drugs"
    assert by_id[45].category == "Skill"

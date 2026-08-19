import pandas as pd

from eve_trader import actions, esi_client, storage


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
    # group_id 303 = Booster for item 44 (currently mislabeled "Implant")
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, {44: 303, 45: 273}[type_id], "x", 0.1, 1, 1, 0, None))

    captured = {}
    monkeypatch.setattr(storage, "upsert_shortlist", lambda items: captured.setdefault("items", items))

    result = actions.do_recategorize_shortlist()

    assert result == {"checked": 2, "recategorized": 1}
    by_id = {i.item_id: i for i in captured["items"]}
    assert by_id[44].category == "Drugs"
    assert by_id[45].category == "Skill"


def test_do_recategorize_shortlist_falls_back_to_esi_for_a_missing_sde_row(monkeypatch):
    # GitHub issue #5 (still broken after the first "shipped" fix): a booster
    # stays mislabeled "Implant" whenever the local SDE cache hasn't been
    # refreshed since this specific type_id was introduced/last touched -
    # storage.get_sde_type returns None, so guess_category never even sees a
    # group_id to check against BOOSTER_GROUP_ID and falls back to the raw
    # category_id's own label ("Implant"). _group_id should fall back to a
    # live ESI lookup instead of leaving the item stuck.
    from eve_trader.models import ShortlistItem

    existing = [ShortlistItem(item="Synth Crash Booster", item_id=28672, category="Implant",
                               volume_m3=0.1, active=True)]
    monkeypatch.setattr(storage, "load_shortlist", lambda: existing)
    monkeypatch.setattr(storage, "load_sde_category_names", lambda: {20: "Implant"})
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 20)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)  # not in the local cache yet
    monkeypatch.setattr(esi_client.ESIClient, "get_type_info", lambda self, type_id: {"group_id": 303})

    captured = {}
    monkeypatch.setattr(storage, "upsert_shortlist", lambda items: captured.setdefault("items", items))

    result = actions.do_recategorize_shortlist()

    assert result == {"checked": 1, "recategorized": 1}
    assert captured["items"][0].category == "Drugs"


def test_do_recategorize_shortlist_stays_implant_when_esi_lookup_also_fails(monkeypatch):
    from eve_trader.models import ShortlistItem

    existing = [ShortlistItem(item="Ocular Filter", item_id=99, category="Implant",
                               volume_m3=0.1, active=True)]
    monkeypatch.setattr(storage, "load_shortlist", lambda: existing)
    monkeypatch.setattr(storage, "load_sde_category_names", lambda: {20: "Implant"})
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 20)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)

    def _raise(self, type_id):
        raise esi_client.ESIError("boom")
    monkeypatch.setattr(esi_client.ESIClient, "get_type_info", _raise)

    captured = {}
    monkeypatch.setattr(storage, "upsert_shortlist", lambda items: captured.setdefault("items", items))

    result = actions.do_recategorize_shortlist()

    assert result == {"checked": 1, "recategorized": 0}
    assert captured["items"][0].category == "Implant"

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
    monkeypatch.setattr(storage, "load_shortlist", lambda: [])

    result = actions.do_add_to_shortlist()

    assert result == {"added": 1, "deferred": 0}
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
    monkeypatch.setattr(storage, "update_snapshot_categories",
                         lambda categories: captured.setdefault("snapshot_categories", categories))

    result = actions.do_recategorize_shortlist()

    assert result == {"checked": 2, "recategorized": 1}
    by_id = {i.item_id: i for i in captured["items"]}
    assert by_id[44].category == "Drugs"
    assert by_id[45].category == "Skill"
    # GitHub issue #5: the already-persisted snapshot the Shortlist page
    # renders must be patched too, not just the live shortlist table.
    assert captured["snapshot_categories"] == {44: "Drugs"}


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
    monkeypatch.setattr(storage, "update_snapshot_categories",
                         lambda categories: captured.setdefault("snapshot_categories", categories))

    result = actions.do_recategorize_shortlist()

    assert result == {"checked": 1, "recategorized": 1}
    assert captured["items"][0].category == "Drugs"
    assert captured["snapshot_categories"] == {28672: "Drugs"}


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
    monkeypatch.setattr(storage, "update_snapshot_categories",
                         lambda categories: captured.setdefault("snapshot_categories", categories))

    result = actions.do_recategorize_shortlist()

    assert result == {"checked": 1, "recategorized": 0}
    assert captured["items"][0].category == "Implant"
    assert captured["snapshot_categories"] == {}


def _candidate_row(type_id: int, item: str, score: float, latest_margin: float, add_flag: int = 1,
                    run_ts: str = "2026-01-01T00:00:00") -> dict:
    return {
        "run_ts": run_ts, "item": item, "category": "Material", "type_id": type_id,
        "volume_m3": 1.0, "paired_days": 5, "profitable_days": 5, "hit_rate": 1.0,
        "latest_margin": latest_margin, "best_margin": latest_margin, "avg_profit_m3": score,
        "avg_sell_movement": 1.0, "score": score, "recommendation": "Consider import",
        "add_flag": add_flag, "meta_level": None,
    }


def test_new_candidates_to_add_keeps_top_n_by_score_and_defers_the_rest():
    # More add_flag=1 newcomers than max_shortlist_growth_per_run allows -
    # only the top N by (score, latest_margin) are taken; the rest stay in
    # new_candidates (this helper doesn't touch storage) for the next run.
    df = pd.DataFrame([
        _candidate_row(1, "Low score", score=1.0, latest_margin=0.4),
        _candidate_row(2, "High score", score=9.0, latest_margin=0.1),
        _candidate_row(3, "Mid score, high margin", score=5.0, latest_margin=0.9),
        _candidate_row(4, "Mid score, low margin", score=5.0, latest_margin=0.2),
        _candidate_row(5, "Not recommended", score=99.0, latest_margin=0.9, add_flag=0),
    ])
    # The action already filters to add_flag=1; this helper sees that slice.
    flagged = df[df["add_flag"] == 1]
    taken, deferred = actions._new_candidates_to_add(flagged, existing_ids=set(), max_growth=2)

    assert deferred == 2
    assert list(taken["type_id"]) == [2, 3]  # 9.0 first, then 5.0 with the higher margin


def test_new_candidates_to_add_does_not_count_existing_shortlist_ids_as_growth():
    df = pd.DataFrame([
        _candidate_row(10, "Already tracked", score=0.1, latest_margin=0.01),
        _candidate_row(11, "New A", score=3.0, latest_margin=0.2),
        _candidate_row(12, "New B", score=2.0, latest_margin=0.2),
        _candidate_row(13, "New C", score=1.0, latest_margin=0.2),
    ])
    taken, deferred = actions._new_candidates_to_add(df, existing_ids={10}, max_growth=1)

    assert deferred == 2  # B and C wait for the next run
    assert set(taken["type_id"]) == {10, 11}  # existing + top newcomer


def test_do_add_to_shortlist_caps_growth_and_leaves_the_rest_in_new_candidates(monkeypatch):
    from eve_trader.config import TradingConfig
    from eve_trader.models import ShortlistItem

    rows = [_candidate_row(i, f"Item {i}", score=float(i), latest_margin=0.1) for i in range(1, 6)]
    monkeypatch.setattr(storage, "read_table", lambda table: pd.DataFrame(rows))
    monkeypatch.setattr(storage, "load_sde_category_names", lambda: {4: "Material"})
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 4)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item {type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "load_shortlist", lambda: [
        ShortlistItem(item="Item 1", item_id=1, category="Material", volume_m3=1.0, active=True),
    ])

    captured = {}
    monkeypatch.setattr(storage, "upsert_shortlist", lambda items: captured.setdefault("items", items))

    result = actions.do_add_to_shortlist(cfg=TradingConfig(max_shortlist_growth_per_run=2))

    # Existing id 1 always upserts; of the 4 newcomers only the top 2 by score
    # (5 and 4) are added; 3 and 2 are deferred. new_candidates itself is not
    # rewritten - the deferred rows keep add_flag=1 for the next run.
    added_ids = {i.item_id for i in captured["items"]}
    assert result == {"added": 3, "deferred": 2}
    assert added_ids == {1, 5, 4}

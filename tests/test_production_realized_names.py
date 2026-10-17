"""Display names for realized-profit warnings (no DB, no ESI)."""
from __future__ import annotations

from eve_trader.production.realized import names


def test_warning_items_get_names_links_and_readable_text(monkeypatch):
    monkeypatch.setattr(names, "type_names", lambda ids: {
        34: "Tritanium", 22444: "Sleipnir", 1318: "Expanded Cargohold I Blueprint",
    })
    monkeypatch.setattr(names, "owner_names", lambda ids: {
        1560510246: "pappmichl", 98723738: "Some Corp",
    })
    monkeypatch.setattr(names, "location_names", lambda ids: {1049588174021: "C-J6MT - Home"})
    jobs = {"job:character:1560510246:675263867": {
        "type_id": 22444, "activity": "Manufacturing", "owner_id": 1560510246,
    }}
    groups = [
        {"kind": "inventory_drift", "count": 1, "items": [
            {"type_id": 34, "ledger_qty": 15.0, "esi_qty": 12.0, "drift": -3.0},
        ]},
        {"kind": "archive_gap", "count": 1, "items": [
            {"owner_type": "corporation", "owner_id": 98723738, "data_kind": "wallet_journal"},
        ]},
        {"kind": "trading_overlap", "count": 1, "items": [
            {"message": "trading_overlap: Production type 34 is also bought on the market"},
        ]},
        {"kind": "unknown_origin", "count": 1, "items": [
            {"message": "unknown_origin: type 1318 (bpc_run) 200 without a layer "
                        "(job:character:1560510246:675263867)"},
        ]},
        {"kind": "coverage_below_threshold", "count": 1, "items": [
            {"message": "coverage_below_threshold: tx:character:1560510246:0:6890579030 "
                        "type 22444 coverage 0%"},
        ]},
        {"kind": "outside_home_structure", "count": 1, "items": [
            {"type_id": 22444, "message": "outside_home_structure: contract:9:22444 type 22444 sold at 1049588174021"},
        ]},
        {"kind": "unresolved_region", "count": 1, "items": [{"location_id": 1049588174021, "count": 2}]},
    ]
    names.label_warnings(groups, jobs)
    items = {g["kind"]: g["items"][0] for g in groups}

    assert items["inventory_drift"]["type_name"] == "Tritanium"
    assert items["archive_gap"]["owner_name"] == "Some Corp"
    overlap = items["trading_overlap"]
    assert overlap["type_id"] == 34 and overlap["type_name"] == "Tritanium"
    assert overlap["text"] == "Tritanium is also bought on the market"
    unknown = items["unknown_origin"]
    # Both a type and a job: the row links to the job.
    assert unknown["job_ref"] == "job:character:1560510246:675263867"
    assert unknown["job_label"] == "job #675263867 (Sleipnir, Manufacturing, pappmichl)"
    assert unknown["text"] == ("Expanded Cargohold I Blueprint (bpc_run) 200 without a layer "
                               "(job #675263867 (Sleipnir, Manufacturing, pappmichl))")
    assert items["coverage_below_threshold"]["text"] == "market sale #6890579030 (pappmichl) Sleipnir coverage 0%"
    assert items["outside_home_structure"]["text"] == "contract #9 Sleipnir sold at C-J6MT - Home"
    assert items["unresolved_region"]["location_name"] == "C-J6MT - Home"


def test_unresolved_ids_stay_visible(monkeypatch):
    monkeypatch.setattr(names, "type_names", lambda ids: {})
    monkeypatch.setattr(names, "owner_names", lambda ids: {})
    monkeypatch.setattr(names, "location_names", lambda ids: {})
    groups = [{"kind": "negative_opening", "count": 1, "items": [
        {"message": "negative_opening: type 1236 opening -7 clamped to 0"},
    ]}]
    names.label_warnings(groups, {})
    item = groups[0]["items"][0]
    assert item["text"] == "type 1236 opening -7 clamped to 0"
    assert "type_name" not in item
    assert item["type_id"] == 1236

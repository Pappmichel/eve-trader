"""Unit tests for production.sde_diff.build_diff - no Postgres, constructed
FetchedSde + snapshot dicts only."""
from eve_trader.production.constants import ACTIVITY_MANUFACTURING
from eve_trader.production.sde import FetchedSde
from eve_trader.production.sde_diff import build_diff


def _type(type_id, name, volume=1.0, meta_group_id=None, published=1):
    return (type_id, 1, name, volume, published, None, None, meta_group_id, 1)


def _snapshot(*, types=None, materials=None, products=None, time=None, probability=None, counts=None):
    return {
        "sde_types": types or [],
        "sde_blueprint_materials": materials or [],
        "sde_blueprint_products": products or [],
        "sde_blueprint_time": time or [],
        "sde_invention_probability": probability or [],
        "counts": counts or {},
    }


def test_build_diff_new_removed_and_changed_items():
    old = _snapshot(types=[
        _type(1, "Rifter", volume=1.0, meta_group_id=1),
        _type(2, "Gone", published=0),
        _type(3, "Same"),
    ])
    fetched = FetchedSde(types=[
        _type(1, "Rifter Mk II", volume=2.5, meta_group_id=4),
        _type(3, "Same"),
        _type(4, "New Ship"),
        _type(5, "Unpublished New", published=0),
    ])

    diff = build_diff(fetched, old)

    assert {(i["type_id"], i["name"]) for i in diff["new_items"]} == {
        (4, "New Ship"), (5, "Unpublished New"),
    }
    assert diff["removed_items"] == [{"type_id": 2, "name": "Gone"}]
    assert len(diff["changed_items"]) == 1
    changed = diff["changed_items"][0]
    assert changed["type_id"] == 1
    assert changed["name"] == "Rifter Mk II"
    assert changed["changes"]["name"] == ["Rifter", "Rifter Mk II"]
    assert changed["changes"]["volume"] == [1.0, 2.5]
    assert changed["changes"]["meta_group_id"] == [1, 4]
    assert 3 not in {i["type_id"] for i in diff["new_items"] + diff["removed_items"] + diff["changed_items"]}


def test_build_diff_changed_blueprint_fields_and_skips_unchanged():
    trit = _type(34, "Tritanium")
    pyro = _type(35, "Pyerite")
    product = _type(587, "Rifter")
    other_product = _type(588, "Rifter I")
    bp = _type(999, "Rifter Blueprint")
    unchanged_bp = _type(1000, "Stable Blueprint")
    unchanged_product = _type(1001, "Stable Hull")
    old = _snapshot(
        types=[trit, pyro, product, other_product, bp, unchanged_bp, unchanged_product],
        materials=[
            (999, ACTIVITY_MANUFACTURING, 34, 1000.0),
            (999, ACTIVITY_MANUFACTURING, 35, 500.0),
            (1000, ACTIVITY_MANUFACTURING, 34, 10.0),
        ],
        products=[
            (999, ACTIVITY_MANUFACTURING, 587, 1.0),
            (1000, ACTIVITY_MANUFACTURING, 1001, 1.0),
        ],
        time=[
            (999, ACTIVITY_MANUFACTURING, 600.0),
            (1000, ACTIVITY_MANUFACTURING, 60.0),
        ],
        probability=[
            (999, 588, 0.3),
            (1000, 1001, 0.4),
        ],
    )
    fetched = FetchedSde(
        types=[trit, pyro, product, other_product, bp, unchanged_bp, unchanged_product],
        blueprint_materials=[
            (999, ACTIVITY_MANUFACTURING, 34, 800.0),
            (999, ACTIVITY_MANUFACTURING, 35, 500.0),
            (1000, ACTIVITY_MANUFACTURING, 34, 10.0),
        ],
        blueprint_products=[
            (999, ACTIVITY_MANUFACTURING, 587, 2.0),
            (1000, ACTIVITY_MANUFACTURING, 1001, 1.0),
        ],
        blueprint_time=[
            (999, ACTIVITY_MANUFACTURING, 450.0),
            (1000, ACTIVITY_MANUFACTURING, 60.0),
        ],
        invention_probability=[
            (999, 588, 0.42),
            (1000, 1001, 0.4),
        ],
    )

    diff = build_diff(fetched, old)

    assert diff["new_items"] == []
    assert diff["removed_items"] == []
    assert diff["changed_items"] == []
    assert [row["blueprint_type_id"] for row in diff["changed_blueprints"]] == [999]
    row = diff["changed_blueprints"][0]
    assert row["product_type_id"] == 587
    assert row["product_name"] == "Rifter"
    assert row["materials"] == [{
        "material_type_id": 34, "name": "Tritanium", "old_qty": 1000.0, "new_qty": 800.0,
    }]
    assert row["products"] == {"old_qty": 1.0, "new_qty": 2.0}
    assert row["time"] == {"old": 600.0, "new": 450.0}
    assert row["invention_probability"] == {"old": 0.3, "new": 0.42}


def test_build_diff_new_blueprint_type_is_new_item_not_changed_blueprint():
    product = _type(10, "Widget")
    bp = _type(11, "Widget Blueprint")
    fetched = FetchedSde(
        types=[product, bp],
        blueprint_materials=[(11, ACTIVITY_MANUFACTURING, 34, 5.0)],
        blueprint_products=[(11, ACTIVITY_MANUFACTURING, 10, 1.0)],
        blueprint_time=[(11, ACTIVITY_MANUFACTURING, 30.0)],
    )
    diff = build_diff(fetched, _snapshot())
    assert {i["type_id"] for i in diff["new_items"]} == {10, 11}
    assert diff["changed_blueprints"] == []


def test_build_diff_ignores_postgres_real_volume_roundtrip():
    # Live Fuzzwork values around 6.5e7 cannot be represented in float32;
    # sde_types.volume is REAL, so a just-applied dump must not show up as
    # changed on the next preview.
    old = _snapshot(types=[_type(21094, "Cynosural Field", volume=65449848.0)])
    fetched = FetchedSde(types=[_type(21094, "Cynosural Field", volume=65449847.0)])
    diff = build_diff(fetched, old)
    assert diff["changed_items"] == []
    assert diff["new_items"] == []
    assert diff["removed_items"] == []


def test_build_diff_table_deltas_skip_types_and_blueprint_tables():
    fetched = FetchedSde(
        groups=[(1, 6, "Frigate")],
        categories=[(6, "Ship")],
        type_slots=[(123, "high")],
    )
    snapshot = _snapshot(counts={
        "sde_groups": 0,
        "sde_market_groups": 0,
        "sde_solar_systems": 0,
        "sde_stations": 0,
        "sde_categories": 0,
        "sde_type_slots": 0,
        "sde_type_materials": 0,
    })
    diff = build_diff(fetched, snapshot)
    assert "sde_types" not in diff["table_deltas"]
    assert "sde_blueprint_materials" not in diff["table_deltas"]
    assert "sde_blueprint_products" not in diff["table_deltas"]
    assert "sde_blueprint_time" not in diff["table_deltas"]
    assert diff["table_deltas"]["sde_groups"] == {"old": 0, "new": 1}
    assert diff["table_deltas"]["sde_categories"] == {"old": 0, "new": 1}
    assert diff["table_deltas"]["sde_type_slots"] == {"old": 0, "new": 1}
    assert diff["table_deltas"]["sde_invention_probability"] == {"old": 0, "new": 0}

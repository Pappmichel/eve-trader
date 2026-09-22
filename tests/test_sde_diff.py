"""Unit tests for production.sde_diff.build_diff - no Postgres, constructed
FetchedSde + snapshot dicts only."""
from eve_trader.production.constants import ACTIVITY_MANUFACTURING, ACTIVITY_REACTION
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


def test_build_diff_published_flag_only_is_a_change():
    old = _snapshot(types=[_type(42, "Rifter", volume=1.0, meta_group_id=1, published=1)])
    fetched = FetchedSde(types=[_type(42, "Rifter", volume=1.0, meta_group_id=1, published=0)])

    diff = build_diff(fetched, old)

    assert diff["new_items"] == []
    assert diff["removed_items"] == []
    assert diff["changed_items"] == [{
        "type_id": 42,
        "name": "Rifter",
        "changes": {"published": [1, 0]},
    }]


def test_build_diff_new_invention_probability_on_existing_blueprint():
    product = _type(588, "Rifter II")
    bp = _type(999, "Rifter Blueprint")
    old = _snapshot(
        types=[product, bp],
        materials=[(999, ACTIVITY_MANUFACTURING, 34, 10.0)],
        products=[(999, ACTIVITY_MANUFACTURING, 588, 1.0)],
        time=[(999, ACTIVITY_MANUFACTURING, 60.0)],
    )
    fetched = FetchedSde(
        types=[product, bp],
        blueprint_materials=[(999, ACTIVITY_MANUFACTURING, 34, 10.0)],
        blueprint_products=[(999, ACTIVITY_MANUFACTURING, 588, 1.0)],
        blueprint_time=[(999, ACTIVITY_MANUFACTURING, 60.0)],
        invention_probability=[(999, 588, 0.35)],
    )

    diff = build_diff(fetched, old)

    assert len(diff["changed_blueprints"]) == 1
    row = diff["changed_blueprints"][0]
    assert row["blueprint_type_id"] == 999
    assert row["materials"] == []
    assert row["products"] is None
    assert row["time"] is None
    assert row["invention_probability"] == {"old": None, "new": 0.35}


def test_build_diff_new_build_time_activity_on_existing_blueprint():
    product = _type(16672, "Tungsten Carbide")
    bp = _type(46207, "Tungsten Carbide Reaction Formula")
    old = _snapshot(
        types=[product, bp],
        products=[(46207, ACTIVITY_MANUFACTURING, 16672, 1.0)],
        time=[(46207, ACTIVITY_MANUFACTURING, 100.0)],
    )
    fetched = FetchedSde(
        types=[product, bp],
        blueprint_products=[(46207, ACTIVITY_MANUFACTURING, 16672, 1.0)],
        blueprint_time=[
            (46207, ACTIVITY_MANUFACTURING, 100.0),
            (46207, ACTIVITY_REACTION, 180.0),
        ],
    )

    diff = build_diff(fetched, old)

    assert len(diff["changed_blueprints"]) == 1
    row = diff["changed_blueprints"][0]
    assert row["blueprint_type_id"] == 46207
    assert row["materials"] == []
    assert row["products"] is None
    assert row["invention_probability"] is None
    assert row["time"] == {"old": None, "new": 180.0}


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


_OTHER_TABLES = (
    "sde_groups",
    "sde_market_groups",
    "sde_solar_systems",
    "sde_stations",
    "sde_categories",
    "sde_type_slots",
    "sde_type_materials",
    "sde_invention_probability",
)


def test_build_diff_other_tables_skip_types_and_blueprint_tables():
    fetched = FetchedSde(
        groups=[(1, 6, "Frigate")],
        categories=[(6, "Ship")],
        type_slots=[(123, "high")],
    )
    diff = build_diff(fetched, _snapshot())
    other = diff["other_tables"]
    assert "sde_types" not in other
    assert "sde_blueprint_materials" not in other
    assert "sde_blueprint_products" not in other
    assert "sde_blueprint_time" not in other
    assert set(other) == set(_OTHER_TABLES)
    assert other["sde_groups"]["new"] == [{"key": "1", "name": "Frigate"}]
    assert other["sde_categories"]["new"] == [{"key": "6", "name": "Ship"}]
    assert other["sde_type_slots"]["new"] == [{"key": "123", "name": "123"}]
    assert other["sde_invention_probability"] == {"new": [], "removed": [], "changed": []}


def test_build_diff_sde_groups_new_removed_changed_skips_unchanged():
    snapshot = _snapshot()
    snapshot["sde_groups"] = [
        (1, 6, "Frigate"),
        (2, 6, "Destroyer"),
        (3, 7, "Ore"),
    ]
    fetched = FetchedSde(groups=[
        (1, 6, "Frigate"),
        (2, 8, "Destroyers"),
        (4, 6, "Cruiser"),
    ])

    groups = build_diff(fetched, snapshot)["other_tables"]["sde_groups"]

    assert groups["new"] == [{"key": "4", "name": "Cruiser"}]
    assert groups["removed"] == [{"key": "3", "name": "Ore"}]
    assert groups["changed"] == [{
        "key": "2",
        "name": "Destroyers",
        "changes": {
            "category_id": [6, 8],
            "group_name": ["Destroyer", "Destroyers"],
        },
    }]
    assert "1" not in {row["key"] for row in groups["new"] + groups["removed"] + groups["changed"]}


def test_build_diff_sde_type_materials_composite_key_and_real_quantity():
    trit = _type(34, "Tritanium")
    pyro = _type(35, "Pyerite")
    mex = _type(36, "Mexallon")
    veld = _type(1230, "Veldspar")
    snapshot = _snapshot(types=[trit, pyro, mex, veld])
    snapshot["sde_type_materials"] = [
        (1230, 34, 415.0),
        (1230, 35, 100.0),
        (1230, 36, 10.0),
    ]
    fetched = FetchedSde(
        types=[trit, pyro, mex, veld],
        type_materials=[
            # Postgres REAL cannot tell 65449847 from 65449848.
            (1230, 34, 65449847.0),
            (1230, 35, 100.0),
            (1228, 34, 50.0),
        ],
    )
    snapshot["sde_type_materials"][0] = (1230, 34, 65449848.0)

    rows = build_diff(fetched, snapshot)["other_tables"]["sde_type_materials"]

    assert rows["new"] == [{"key": "1228:34", "name": "1228 → Tritanium"}]
    assert rows["removed"] == [{"key": "1230:36", "name": "Veldspar → Mexallon"}]
    assert rows["changed"] == []
    assert "1230:35" not in {row["key"] for row in rows["new"] + rows["removed"] + rows["changed"]}

    fetched_changed = FetchedSde(
        types=[trit, pyro, mex, veld],
        type_materials=[
            (1230, 34, 400.0),
            (1230, 35, 100.0),
            (1230, 36, 10.0),
        ],
    )
    changed = build_diff(fetched_changed, snapshot)["other_tables"]["sde_type_materials"]
    assert changed["new"] == []
    assert changed["removed"] == []
    assert changed["changed"] == [{
        "key": "1230:34",
        "name": "Veldspar → Tritanium",
        "changes": {"quantity": [65449848.0, 400.0]},
    }]


def test_build_diff_sde_invention_probability_composite_key_not_deduped():
    bp = _type(999, "Rifter Blueprint")
    rifter_ii = _type(588, "Rifter II")
    stabber_ii = _type(589, "Stabber II")
    snapshot = _snapshot(
        types=[bp, rifter_ii, stabber_ii],
        probability=[(999, 588, 0.3), (999, 589, 0.2)],
    )
    fetched = FetchedSde(
        types=[bp, rifter_ii, stabber_ii, _type(1000, "Stabber Blueprint")],
        invention_probability=[
            (999, 588, 0.42),
            (1000, 589, 0.15),
        ],
    )

    diff = build_diff(fetched, snapshot)
    rows = diff["other_tables"]["sde_invention_probability"]

    assert rows["new"] == [{"key": "1000:589", "name": "Stabber Blueprint → Stabber II"}]
    assert rows["removed"] == [{"key": "999:589", "name": "Rifter Blueprint → Stabber II"}]
    assert rows["changed"] == [{
        "key": "999:588",
        "name": "Rifter Blueprint → Rifter II",
        "changes": {"probability": [0.3, 0.42]},
    }]
    # The per-blueprint summary still reports the same probability change.
    assert len(diff["changed_blueprints"]) == 1
    assert diff["changed_blueprints"][0]["invention_probability"] == {"old": 0.3, "new": 0.42}

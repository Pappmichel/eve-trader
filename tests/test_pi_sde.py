"""PI SDE import helpers (production/sde.py) - pure, no network, no Postgres -
plus the Postgres-backed storage round trip of the PI tables."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.production import sde

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401


def _planet(item_id, type_id, group_id=7, system="30000142", radius="6440000", name="Jita IV"):
    return {
        "itemID": str(item_id), "typeID": str(type_id), "groupID": str(group_id),
        "solarSystemID": system, "constellationID": "20000020", "regionID": "10000002",
        "orbitID": "", "x": "0", "y": "0", "z": "0", "radius": radius,
        "itemName": name, "security": "0.9", "celestialIndex": "4", "orbitIndex": "",
    }


def test_pi_planet_rows_whitelist_radius_and_skips():
    rows = [
        _planet(1, 11),
        _planet(2, 2063, radius="1000"),
        _planet(3, 30889),                         # Shattered planet
        _planet(4, 56018),
        _planet(5, 11, group_id=8),                # not a planet
        _planet(6, 12, radius=""),
        _planet(7, 13, system=""),
        _planet(8, 2014, name=""),
    ]
    out = sde.pi_planet_rows(rows)
    assert out == [
        (1, "Jita IV", 30000142, 11, 6440.0),
        (2, "Jita IV", 30000142, 2063, 1.0),
        (8, None, 30000142, 2014, 6440.0),
    ]
    for type_id in (11, 12, 13, 2014, 2015, 2016, 2017, 2063):
        assert sde.pi_planet_rows([_planet(9, type_id)])


def _attr(type_id, attr_id, value_int="", value_float=""):
    return {"typeID": str(type_id), "attributeID": str(attr_id),
            "valueInt": value_int, "valueFloat": value_float}


def test_split_attribute_rows_skill_and_pi_filters():
    pi_types = frozenset({2544})
    pi_attrs = frozenset({49, 1641})
    rows = [
        _attr(3380, 275, value_float="1.0"),       # skillTimeConstant: kept for any type
        _attr(2544, 49, value_float="500"),        # PI attr on PI type
        _attr(2544, 1641, value_int="3"),
        _attr(587, 49, value_float="40"),          # generic attr on a non-PI type
        _attr(2544, 1641),                         # empty value
        _attr(2544, 9999, value_float="1"),        # irrelevant attribute
    ]
    skill, pi = sde.split_attribute_rows(rows, pi_types, pi_attrs)
    assert skill == {3380: {275: 1.0}}
    assert pi == {2544: {49: 500.0, 1641: 3.0}}


def test_pi_type_ids_from_categories_41_42_43_only():
    groups = [
        {"groupID": "1", "categoryID": "41", "groupName": "Planetary Links"},
        {"groupID": "2", "categoryID": "42", "groupName": "Planetary Resources"},
        {"groupID": "3", "categoryID": "43", "groupName": "Planetary Commodities"},
        {"groupID": "4", "categoryID": "6", "groupName": "Frigate"},
        {"groupID": "5", "categoryID": "", "groupName": "Odd"},
    ]
    types = [
        {"typeID": "10", "groupID": "1"}, {"typeID": "11", "groupID": "2"},
        {"typeID": "12", "groupID": "3"}, {"typeID": "13", "groupID": "4"},
        {"typeID": "14", "groupID": "5"}, {"typeID": "15", "groupID": ""},
    ]
    assert sde.pi_type_ids_from(types, groups) == frozenset({10, 11, 12})


def test_pi_rows_from_csv_types_bools_and_base_price():
    schematics = [{"schematicID": "65", "schematicName": "Bacteria", "cycleTime": "1800"}]
    type_map = [
        {"schematicID": "65", "typeID": "2393", "quantity": "20", "isInput": "1"},
        {"schematicID": "65", "typeID": "2393", "quantity": "5", "isInput": "0"},
    ]
    pin_map = [{"schematicID": "65", "pinTypeID": "2492"}]
    inv_types = [
        {"typeID": "2393", "groupID": "1", "basePrice": "400.5"},
        {"typeID": "34", "groupID": "2", "basePrice": "9.0"},      # not a PI type
        {"typeID": "2492", "groupID": "3", "basePrice": "", "capacity": "10000.0"},
    ]
    sch, tm, pins, attrs = sde.pi_rows_from_csv(
        schematics, type_map, pin_map, {2393: {49: 1.5}}, inv_types, frozenset({2393, 2492}),
    )
    assert sch == [(65, "Bacteria", 1800)]
    assert tm == [(65, 2393, 20, True), (65, 2393, 5, False)]
    assert pins == [(65, 2492)]
    # basePrice -> pseudo attribute -1, invTypes.capacity -> -2 (launchpad/storage capacity)
    assert sorted(attrs) == [(2393, -1, 400.5), (2393, 49, 1.5), (2492, -2, 10000.0)]


# ------------------------------------------------------------ storage round trip
@pg_helpers.postgres_required()
def test_replace_sde_data_stores_and_replaces_the_pi_tables_and_readers(tenant):
    base = dict(blueprint_time=[], blueprint_materials=[], blueprint_products=[], market_groups=[])
    storage.replace_sde_data(
        types=[(2393, 1334, "Bacteria", 0.38, 1, None, None, None, 1)],
        groups=[(1334, 43, "Basic Commodities")],
        solar_systems=[(30000142, "Jita", 0.9, 10000002), (30000144, "Perimeter", 0.9, 10000002)],
        pi_schematics=[(65, "Bacteria", 1800)],
        pi_schematic_types=[(65, 2393, 5, False)],
        pi_schematic_pins=[(65, 2492)],
        pi_type_attributes=[(2393, -1, 400.5)],
        pi_planets=[
            (40009077, "Jita IV", 30000142, 11, 6440.0),
            (40009078, "Jita V", 30000142, 12, 3000.0),
            (40009100, "Perimeter I", 30000144, 11, 7000.0),
        ],
        **base,
    )
    counts = storage.sde_row_counts()
    assert counts["sde_pi_schematics"] == 1 and counts["sde_pi_planets"] == 3
    assert counts["sde_pi_schematic_types"] == 1 and counts["sde_pi_schematic_pins"] == 1
    assert counts["sde_pi_type_attributes"] == 1

    rows = storage.load_pi_static_rows()
    assert rows["schematics"] == [(65, "Bacteria", 1800)]
    assert rows["schematic_types"] == [(65, 2393, 5, False)]
    assert rows["schematic_pins"] == [(65, 2492)]
    assert rows["attributes"] == [(2393, -1, 400.5)]
    assert rows["types"] == [(2393, "Bacteria", 1334, 43, pytest.approx(0.38))]

    assert storage.pi_planets_in_system(30000142) == [
        (40009077, "Jita IV", 11, 6440.0), (40009078, "Jita V", 12, 3000.0),
    ]
    assert storage.get_pi_planet(40009100) == (40009100, "Perimeter I", 30000144, 11, 7000.0)
    assert storage.get_pi_planet(1) is None
    assert storage.get_solar_system(30000142) == (30000142, "Jita", pytest.approx(0.9), 10000002)
    assert storage.get_solar_system(1) is None
    found = storage.search_pi_systems("jit")
    assert [r[:2] for r in found] == [(30000142, "Jita")] and found[0][4] == 2
    assert storage.search_pi_systems("") == []
    assert storage.pi_planet_radius_medians()[11] == pytest.approx(6720.0)

    storage.replace_sde_data(types=[], groups=[], **base)
    counts = storage.sde_row_counts()
    assert all(counts[t] == 0 for t in (
        "sde_pi_schematics", "sde_pi_schematic_types", "sde_pi_schematic_pins",
        "sde_pi_type_attributes", "sde_pi_planets",
    ))

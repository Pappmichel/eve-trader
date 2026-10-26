"""Location diagnostics (docs/LOCATION_RESOLUTION_PLAN.md phase 1)."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.locations import diagnostics as d

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

STATION, OFFICE_ID = 60099991, 1050055429685
KNOWN, FAILED_ID, NEW_ID, ORDER_ONLY = 1049865184988, 1049865184989, 1049865184990, 1049865184991
CONTAINER = 1049865184999


def test_classify_separates_stations_systems_offices_and_structures():
    stations, offices = {STATION}, {OFFICE_ID}
    assert d.classify(STATION, stations, offices) == "npc_station"
    assert d.classify(30000142, stations, offices) == "solar_system"
    assert d.classify(OFFICE_ID, stations, offices) == "office"
    assert d.classify(KNOWN, stations, offices) == "structure"
    assert d.classify(5_000_000, stations, offices) == "other"
    assert d.classify(CONTAINER, stations, offices, {CONTAINER}) == "container"


def test_report_counts_states_sources_and_esi_cost():
    sources = {
        "corp_assets": {STATION, OFFICE_ID, KNOWN, FAILED_ID, CONTAINER},
        "corp_industry_jobs": {OFFICE_ID},
        "sell_orders": {KNOWN, ORDER_ONLY},
        "ledger_contracts_end": {NEW_ID},
    }
    state = {KNOWN: d.RESOLVED, FAILED_ID: d.FAILED}  # NEW_ID / ORDER_ONLY never attempted

    r = d.build_report(sources, {STATION}, {OFFICE_ID}, {CONTAINER}, state, capable_characters=2, corporations=1)

    assert r["totals"]["by_kind"]["structure"] == 4 and r["totals"]["by_kind"]["office"] == 1
    assert r["structures"] == {d.RESOLVED: 1, d.MANUAL: 0, d.FAILED: 1, d.NEVER: 2}
    # 1 corporation list + (1 failed + 2 never attempted) ids x 2 characters
    assert r["estimated_esi_calls_per_sync"] == 1 + 3 * 2
    assert r["totals"]["by_kind"]["container"] == 1
    assert r["by_source"]["sell_orders"]["covered_by_bulk_resolve"] is False
    assert r["by_source"]["corp_assets"]["covered_by_bulk_resolve"] is True
    by_id = {u["location_id"]: u for u in r["unresolved"]}
    assert by_id[OFFICE_ID]["kind"] == "office"
    assert by_id[FAILED_ID]["reachable_by_bulk_resolve"] is True
    assert by_id[ORDER_ONLY]["reachable_by_bulk_resolve"] is False
    assert by_id[NEW_ID]["reachable_by_bulk_resolve"] is False  # only a ledger contract references it
    assert CONTAINER not in by_id  # resolved through its parent, not an unresolved name
    assert KNOWN not in by_id and STATION not in by_id
    # ids the bulk resolver can reach come first
    assert r["unresolved"][0]["reachable_by_bulk_resolve"] is True


pytestmark_db = pg_helpers.postgres_required()


@pytestmark_db
def test_collect_reads_every_source_for_the_tenant(tenant, _apply_esi_access_schema):  # noqa: F811
    pg_helpers.wipe_tables(
        "corp_assets", "character_sell_orders", "structure_names", "global_structure_names",
        "manual_location_names",
    )
    storage.replace_assets("corp_assets", [
        (OFFICE_ID, storage.OFFICE_TYPE_ID, KNOWN, "OfficeFolder", 1, 0, "Corp (corp)", True),
        (7, 34, OFFICE_ID, "CorpSAG1", 5, 0, "Corp (corp)"),
    ], owner_corporation_id=94099, owner_name="Corp (corp)")
    storage.replace_sell_orders([(5001, 34, ORDER_ONLY, 10000002, 3, "Alice")], owner_character_id=91099)
    storage.set_cached_structure_name(KNOWN, "Fort Test", 30000142)
    storage.set_cached_structure_name(ORDER_ONLY, None)

    r = d.collect(capable_characters=1, corporations=1)

    assert r["by_source"]["sell_orders"]["ids"] == 1
    assert r["structures"][d.RESOLVED] == 1 and r["structures"][d.FAILED] == 1
    assert {u["location_id"] for u in r["unresolved"]} == {OFFICE_ID, ORDER_ONLY}

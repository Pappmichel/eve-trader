from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.doctrine import actions

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, tenant, tenant_pair  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_DOCTRINE_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "doctrine_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_doctrine_schema(_apply_phase1_schema, _apply_phase2_schema):
    """docs/doctrine_schema.sql, applied once per session via the owner role
    - same pattern as _apply_phase2_schema/_apply_phase3_schema (see
    pg_helpers.py). Depends on _apply_phase2_schema too, not just phase1 -
    this file's own ALTER TABLE tenant_settings widening needs that table
    to already exist."""
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_DOCTRINE_SCHEMA_SQL.read_text(encoding="utf-8"))


def test_doctrine_and_fitting_crud(tenant):
    doctrine_id = storage.create_doctrine("Test Doctrine", "desc")
    assert storage.get_doctrine(doctrine_id)[1] == "Test Doctrine"

    fitting_id = storage.create_fitting(doctrine_id, "Test Fit", 587, "[Rifter, Test Fit]\n", None, 2, 3, None)
    storage.replace_fitting_items(fitting_id, [(1, "low", 2048, 1, False), (2, "high", 2889, 1, False)])
    items = storage.load_fitting_items(fitting_id)
    assert items == [(1, "low", 2048, 1.0, False), (2, "high", 2889, 1.0, False)]

    storage.update_doctrine(doctrine_id, {"name": "Renamed"})
    assert storage.get_doctrine(doctrine_id)[1] == "Renamed"

    storage.delete_fitting(fitting_id)
    assert storage.load_fitting_items(fitting_id) == []
    storage.delete_doctrine(doctrine_id)
    assert storage.get_doctrine(doctrine_id) is None


# Large, unlikely-to-collide fake IDs - sde_types/sde_groups are shared,
# non-tenant-scoped reference tables (see phase1_schema.sql's own "shared
# tables" bucket), so this seeds additional rows alongside whatever real SDE
# data already exists rather than wiping/replacing anything.
_FAKE_HULL_TYPE_ID = 900001
_FAKE_FUEL_TYPE_ID = 900002
_FAKE_SHIP_GROUP_ID = 900101
_FAKE_MATERIAL_GROUP_ID = 900102


@pytest.fixture
def _fake_capital_sde():
    # resolve_sde_type_by_name INNER JOINs sde_types.group_id -> sde_groups
    # - a NULL group_id would never match, so both fake types need a real
    # (fake) group row, not just the ship hull.
    with pg_helpers.psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO sde_groups (group_id, category_id, group_name) VALUES (%s, 6, 'Fake Dreadnought') "
            "ON CONFLICT (group_id) DO UPDATE SET category_id = excluded.category_id",
            (_FAKE_SHIP_GROUP_ID,),
        )
        conn.execute(
            "INSERT INTO sde_groups (group_id, category_id, group_name) VALUES (%s, 4, 'Fake Fuel Material') "
            "ON CONFLICT (group_id) DO UPDATE SET category_id = excluded.category_id",
            (_FAKE_MATERIAL_GROUP_ID,),
        )
        conn.execute(
            "INSERT INTO sde_types (type_id, group_id, type_name, published) VALUES (%s, %s, 'Fake Capital Hull', 1) "
            "ON CONFLICT (type_id) DO UPDATE SET group_id = excluded.group_id",
            (_FAKE_HULL_TYPE_ID, _FAKE_SHIP_GROUP_ID),
        )
        conn.execute(
            "INSERT INTO sde_types (type_id, group_id, type_name, published) VALUES (%s, %s, 'Fake Fuel Block', 1) "
            "ON CONFLICT (type_id) DO UPDATE SET group_id = excluded.group_id",
            (_FAKE_FUEL_TYPE_ID, _FAKE_MATERIAL_GROUP_ID),
        )
    yield


def test_do_add_fitting_persists_and_parses_fuel_bay_text(tenant, _fake_capital_sde):
    # GitHub issue #18 - end-to-end wiring test: fuel_bay_text is stored
    # verbatim on the fitting row *and* parsed into a real "fuelbay" item,
    # merged alongside the main EFT body's own items in one combined
    # replace_fitting_items call.
    doctrine_id = storage.create_doctrine("Capital Doctrine", None)
    result = actions.do_add_fitting(
        doctrine_id, raw_eft="[Fake Capital Hull, Test Dread]\n",
        fuel_bay_text="Fake Fuel Block x2500",
    )

    fitting = result["fitting"]
    assert fitting["fuel_bay_text"] == "Fake Fuel Block x2500"
    assert fitting["ship_maintenance_bay_text"] is None

    items = storage.load_fitting_items(fitting["fitting_id"])
    fuelbay_items = [i for i in items if i[1] == "fuelbay"]
    assert len(fuelbay_items) == 1
    assert fuelbay_items[0][2] == _FAKE_FUEL_TYPE_ID  # type_id
    assert fuelbay_items[0][3] == 2500.0  # quantity


def test_do_update_fitting_keeps_existing_fuel_bay_text_when_only_raw_eft_changes(tenant, _fake_capital_sde):
    # GitHub issue #18: editing just the EFT body (e.g. swapping a module)
    # must not silently wipe an already-set fuel_bay_text - the two are
    # meant to be editable independently even though they share one
    # combined replace_fitting_items call under the hood.
    doctrine_id = storage.create_doctrine("Capital Doctrine", None)
    added = actions.do_add_fitting(
        doctrine_id, raw_eft="[Fake Capital Hull, Test Dread]\n",
        fuel_bay_text="Fake Fuel Block x2500",
    )
    fitting_id = added["fitting"]["fitting_id"]

    updated = actions.do_update_fitting(fitting_id, raw_eft="[Fake Capital Hull, Test Dread v2]\n")

    assert updated["fitting"]["fuel_bay_text"] == "Fake Fuel Block x2500"
    items = storage.load_fitting_items(fitting_id)
    fuelbay_items = [i for i in items if i[1] == "fuelbay"]
    assert len(fuelbay_items) == 1
    assert fuelbay_items[0][3] == 2500.0


def test_fitting_items_same_line_no_two_items_do_not_collide(tenant):
    # Confirmed real bug caught during implementation: a comma-split EFT
    # line (module + its paired charge) produces two FittingItems sharing
    # one line_no - replace_fitting_items must discriminate them itself.
    doctrine_id = storage.create_doctrine("D", None)
    fitting_id = storage.create_fitting(doctrine_id, "F", 587, "[Rifter, F]\n", None, 0, 0, None)
    storage.replace_fitting_items(fitting_id, [
        (3, "high", 2889, 1, False),
        (3, "charge", 222, 1, False),
    ])
    items = storage.load_fitting_items(fitting_id)
    assert len(items) == 2
    assert {i[2] for i in items} == {2889, 222}


def test_replace_fitting_items_is_idempotent_replace_not_append(tenant):
    doctrine_id = storage.create_doctrine("D", None)
    fitting_id = storage.create_fitting(doctrine_id, "F", 587, "[Rifter, F]\n", None, 0, 0, None)
    storage.replace_fitting_items(fitting_id, [(1, "low", 100, 1, False)])
    storage.replace_fitting_items(fitting_id, [(1, "low", 200, 1, False)])
    items = storage.load_fitting_items(fitting_id)
    assert items == [(1, "low", 200, 1.0, False)]


def test_unmatch_contracts_for_fitting_clears_match_and_deviations(tenant):
    doctrine_id = storage.create_doctrine("D", None)
    fitting_id = storage.create_fitting(doctrine_id, "F", 587, "[Rifter, F]\n", None, 0, 0, None)
    storage.replace_doctrine_sync_snapshot(
        contracts=[(1, "doctrine:1", False, 1, 12345, "outstanding", "t", 1.0, None, fitting_id, 0.9, "valid",
                    "2026-01-01T00:00:00Z")],
        items=[(1, 1, 587, 1, True, True)],
        deviations=[(1, 100, "missing", 1, 0, "critical")],
    )
    storage.unmatch_contracts_for_fitting(fitting_id)
    rows = storage.list_doctrine_contracts()
    assert rows[0][9] is None  # matched_fitting_id
    assert rows[0][11] == "unmatched"  # validation_status
    assert storage.load_doctrine_contract_deviations(1) == []


def test_doctrine_settings_scope_accepted_by_tenant_settings_check(tenant):
    # Confirmed real gap during implementation: tenant_settings.scope's
    # check constraint only allowed 'trading'/'production' until
    # doctrine_schema.sql widened it.
    storage.save_tenant_settings("doctrine", {"cargo_tolerance_pct": 0.8})
    assert storage.load_tenant_settings("doctrine") == {"cargo_tolerance_pct": 0.8}


def test_contracts_are_tenant_isolated_even_with_same_contract_id(tenant_pair):
    # The real reason doctrine_contracts' PK is composite (tenant_id,
    # contract_id): two tenants in the same corp can both legitimately see
    # (and store) a row for the identical ESI contract_id.
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.replace_doctrine_sync_snapshot(
            contracts=[(42, "doctrine:1", False, 1, 1, "outstanding", "A's view", 1.0, None, None, None,
                        "unmatched", "2026-01-01T00:00:00Z")],
            items=[], deviations=[],
        )
    with storage.tenant_context(tenant_b):
        storage.replace_doctrine_sync_snapshot(
            contracts=[(42, "doctrine:2", False, 1, 1, "outstanding", "B's view", 1.0, None, None, None,
                        "unmatched", "2026-01-01T00:00:00Z")],
            items=[], deviations=[],
        )

    with storage.tenant_context(tenant_a):
        rows_a = storage.list_doctrine_contracts()
    with storage.tenant_context(tenant_b):
        rows_b = storage.list_doctrine_contracts()

    assert len(rows_a) == 1 and rows_a[0][6] == "A's view"
    assert len(rows_b) == 1 and rows_b[0][6] == "B's view"


@pytest.fixture(autouse=True)
def _wipe_doctrine_assets():
    # doctrine_character_assets/doctrine_corp_assets are column-only-bucket
    # tables (PK = item_id alone - same shape as Production's own
    # character_assets/corp_assets, see test_storage_stock.py's own _wipe
    # fixture for why that matters across tests reusing small item_ids).
    # character_assets is wiped here too - a couple of tests below write
    # directly into it (to prove Doctrine's own reads ignore Production's
    # tables) with the same small hardcoded item_ids test_storage_stock.py
    # uses; without wiping it here, a leftover row from an earlier session
    # collides on the physical PK the next time this file runs (confirmed
    # real flake: replace_assets' own DELETE is RLS-scoped to the *current*
    # tenant, but item_id's PK isn't tenant-scoped, so a different tenant's
    # already-committed row isn't deleted and blocks the INSERT).
    pg_helpers.wipe_tables("doctrine_character_assets", "doctrine_corp_assets", "character_assets", "corp_assets")
    yield


def test_has_any_doctrine_synced_assets_false_until_synced(tenant):
    assert storage.has_any_doctrine_synced_assets() is False

    storage.replace_assets("doctrine_character_assets", [(1, 34, 1000000000001, "Hangar", 100, 0, "pilot")])

    assert storage.has_any_doctrine_synced_assets() is True


def test_has_any_doctrine_synced_assets_ignores_productions_own_tables(tenant):
    # Doctrine's Stockpile must work standalone, without Production ever
    # having synced anything - the whole point of the architecture reversal
    # this table pair exists for (see has_any_doctrine_synced_assets' own
    # docstring in storage.py).
    storage.replace_assets("character_assets", [(1, 34, 1000000000001, "Hangar", 100, 0, "pilot")])

    assert storage.has_any_doctrine_synced_assets() is False


def test_esi_stock_at_location_reads_doctrines_own_asset_tables(tenant):
    type_id, location_id = 34, 1000000000001
    storage.replace_assets("doctrine_character_assets", [(1, type_id, location_id, "Hangar", 100, 0, "pilot")])
    storage.replace_assets("doctrine_corp_assets", [(2, type_id, location_id, "Hangar", 50, 0, "My Corp (corp)")])
    # Production's own tables have unrelated stock at the same location -
    # must not leak into a Doctrine-scoped read.
    storage.replace_assets("character_assets", [(3, type_id, location_id, "Hangar", 999, 0, "pilot")])

    doctrine_tables = ("doctrine_character_assets", "doctrine_corp_assets")
    assert storage.esi_stock_at_location(type_id, location_id, tables=doctrine_tables) == 150


def _history_row(**overrides) -> tuple:
    base = dict(contract_id=1, source_role="doctrine:1560510246", fitting_id=None, fitting_name="Fit",
                hull_type_id=1000, title="t", price=100.0, acceptor_id=99, acceptor_name="Buyer",
                date_issued="2026-01-01T00:00:00Z", date_completed="2026-01-02T00:00:00Z")
    base.update(overrides)
    fields = ("contract_id", "source_role", "fitting_id", "fitting_name", "hull_type_id", "title", "price",
              "acceptor_id", "acceptor_name", "date_issued", "date_completed")
    return tuple(base[f] for f in fields)


def test_upsert_doctrine_contract_history_round_trips(tenant):
    storage.upsert_doctrine_contract_history([_history_row()])

    rows = storage.load_doctrine_contract_history()

    # date_issued/date_completed round-trip as tz-aware datetimes (TIMESTAMPTZ
    # columns, not TEXT) rather than the original ISO strings - compared
    # separately below, everything else compared verbatim.
    assert len(rows) == 1
    assert rows[0][:9] == _history_row()[:9]
    assert rows[0][9].isoformat() == "2026-01-01T00:00:00+00:00"
    assert rows[0][10].isoformat() == "2026-01-02T00:00:00+00:00"


def test_upsert_doctrine_contract_history_empty_list_is_a_no_op(tenant):
    # GitHub issue #19: sync_contracts calls this unconditionally every run
    # (all_history_contracts is usually empty - most syncs have no newly-
    # finished contract) - must not error on an empty executemany batch.
    storage.upsert_doctrine_contract_history([])

    assert storage.load_doctrine_contract_history() == []


def test_upsert_doctrine_contract_history_updates_on_conflict(tenant):
    # A later sync resolving an acceptor_name that failed to resolve the
    # first time (see storage.upsert_doctrine_contract_history's own
    # docstring) must update the existing row, not skip/duplicate it.
    storage.upsert_doctrine_contract_history([_history_row(acceptor_name=None)])
    storage.upsert_doctrine_contract_history([_history_row(acceptor_name="Buyer")])

    rows = storage.load_doctrine_contract_history()

    assert len(rows) == 1
    assert rows[0][8] == "Buyer"  # acceptor_name


def test_load_doctrine_contract_history_orders_most_recently_completed_first(tenant):
    storage.upsert_doctrine_contract_history([
        _history_row(contract_id=1, date_completed="2026-01-01T00:00:00Z"),
        _history_row(contract_id=2, date_completed="2026-01-03T00:00:00Z"),
        _history_row(contract_id=3, date_completed="2026-01-02T00:00:00Z"),
    ])

    rows = storage.load_doctrine_contract_history()

    assert [r[0] for r in rows] == [2, 3, 1]


def test_contract_history_tenant_isolated_even_with_same_contract_id(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.upsert_doctrine_contract_history([_history_row(contract_id=1, acceptor_name="A's buyer")])
    with storage.tenant_context(tenant_b):
        storage.upsert_doctrine_contract_history([_history_row(contract_id=1, acceptor_name="B's buyer")])

    with storage.tenant_context(tenant_a):
        rows_a = storage.load_doctrine_contract_history()
    with storage.tenant_context(tenant_b):
        rows_b = storage.load_doctrine_contract_history()

    assert len(rows_a) == 1 and rows_a[0][8] == "A's buyer"
    assert len(rows_b) == 1 and rows_b[0][8] == "B's buyer"

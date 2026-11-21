"""An item moved between two owners of one tenant must not count twice while
the old owner's snapshot is stale, and the old owner is synced at the next
tick (storage._remove_moved_items / _request_owner_refresh)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import storage
from eve_trader.esi_data import orchestrator

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

ALICE, BOB, CORP = 10, 20, 98000001
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(
        "character_assets", "corp_assets", "character_blueprints", "corp_blueprints",
        "esi_freshness",
    )
    yield


def _asset(item_id, type_id=587, qty=1, singleton=True, flag="Hangar", owner="Alice"):
    return (item_id, type_id, 60003760, flag, qty, 0, owner, singleton)


def _assets(table="character_assets"):
    with storage.connect() as conn:
        return sorted(
            (int(r[0]), r[1], r[2] or r[3]) for r in conn.execute(
                f"SELECT item_id, owner_name, owner_character_id, owner_corporation_id FROM {table}"
            ).fetchall()
        )


def _synced(owner_type, owner_id, kind):
    storage.upsert_esi_freshness(owner_type, owner_id, kind, success=True, now=T0.isoformat())


def _requests():
    return set(storage.list_esi_refresh_requests())


def test_ship_moved_from_character_to_corp_counts_once(tenant):
    storage.replace_assets("character_assets", [_asset(1), _asset(2)],
                           owner_character_id=ALICE, owner_name="Alice")
    _synced("character", ALICE, "assets")

    storage.replace_assets("corp_assets", [_asset(1, flag="CorpSAG7", owner="Corp (corp)")],
                           owner_corporation_id=CORP, owner_name="Corp (corp)")

    assert _assets("character_assets") == [(2, "Alice", ALICE)]
    assert _assets("corp_assets") == [(1, "Corp (corp)", CORP)]
    assert _requests() == {("character", ALICE, "assets")}


def test_stack_moved_whole_between_characters_counts_once(tenant):
    storage.replace_assets("character_assets", [_asset(5, type_id=34, qty=1000, singleton=False)],
                           owner_character_id=ALICE, owner_name="Alice")
    storage.replace_assets("character_assets",
                           [_asset(5, type_id=34, qty=1000, singleton=False, owner="Bob")],
                           owner_character_id=BOB, owner_name="Bob")

    assert _assets() == [(5, "Bob", BOB)]


def test_stacks_sharing_an_item_id_with_other_quantities_stay(tenant):
    # c25e265: two owners' stacks with one item_id but different quantities.
    storage.replace_assets("character_assets", [_asset(5, type_id=34, qty=1000, singleton=False)],
                           owner_character_id=ALICE, owner_name="Alice")
    storage.replace_assets("character_assets",
                           [_asset(5, type_id=34, qty=400, singleton=False, owner="Bob")],
                           owner_character_id=BOB, owner_name="Bob")

    assert _assets() == [(5, "Alice", ALICE), (5, "Bob", BOB)]


def test_same_item_id_with_another_type_stays(tenant):
    storage.replace_assets("character_assets", [_asset(7, type_id=587)],
                           owner_character_id=ALICE, owner_name="Alice")
    storage.replace_assets("character_assets", [_asset(7, type_id=588, owner="Bob")],
                           owner_character_id=BOB, owner_name="Bob")

    assert _assets() == [(7, "Alice", ALICE), (7, "Bob", BOB)]


def test_blueprint_moved_from_character_to_corp_counts_once(tenant):
    storage.replace_blueprints("character_blueprints", [(3, 999, 1, "Hangar", -1, 10, 20, -1)],
                               owner_character_id=ALICE)
    _synced("character", ALICE, "blueprints")

    storage.replace_blueprints("corp_blueprints", [(3, 999, 1, "CorpSAG1", -1, 10, 20, -1)],
                               owner_corporation_id=CORP)

    with storage.connect() as conn:
        assert conn.execute("SELECT count(*) FROM character_blueprints").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM corp_blueprints").fetchone()[0] == 1
    assert _requests() == {("character", ALICE, "blueprints")}


def test_blueprint_moved_between_characters_requests_the_old_owner(tenant):
    storage.replace_blueprints("character_blueprints", [(3, 999, 1, "Hangar", -2, 0, 0, 5)],
                               owner_character_id=ALICE)
    _synced("character", ALICE, "blueprints")

    storage.replace_blueprints("character_blueprints", [(3, 999, 1, "Hangar", -2, 0, 0, 5)],
                               owner_character_id=BOB)

    assert _requests() == {("character", ALICE, "blueprints")}


def test_refresh_request_makes_the_pair_due_until_the_next_attempt(tenant):
    storage.replace_assets("character_assets", [_asset(1)],
                           owner_character_id=ALICE, owner_name="Alice")
    _synced("character", ALICE, "assets")
    in_an_hour = T0 + timedelta(hours=1)

    def due():
        return orchestrator._kind_is_due(
            "character", ALICE, "assets", now=in_an_hour,
            freshness=orchestrator._freshness_map(),
        )

    assert due() is False  # inside the 6 h tier interval
    storage.replace_assets("corp_assets", [_asset(1, owner="Corp (corp)")],
                           owner_corporation_id=CORP, owner_name="Corp (corp)")
    assert due() is True

    later = datetime.now(timezone.utc) + timedelta(minutes=1)
    storage.upsert_esi_freshness("character", ALICE, "assets", success=True, now=later.isoformat())
    assert _requests() == set()


def test_refresh_request_still_waits_for_esi_expires(tenant):
    storage.replace_assets("character_assets", [_asset(1)],
                           owner_character_id=ALICE, owner_name="Alice")
    storage.upsert_esi_freshness("character", ALICE, "assets", success=True, now=T0.isoformat(),
                                 next_fetch_after=T0 + timedelta(hours=2))
    storage.replace_assets("corp_assets", [_asset(1, owner="Corp (corp)")],
                           owner_corporation_id=CORP, owner_name="Corp (corp)")

    assert orchestrator._kind_is_due(
        "character", ALICE, "assets", now=T0 + timedelta(hours=1),
        freshness=orchestrator._freshness_map(),
    ) is False

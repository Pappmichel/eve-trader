import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TYPE_ID = 999
LOCATION_ID = 1000000000001


@pytest.fixture(autouse=True)
def _wipe():
    # character_blueprints/corp_blueprints are column-only-bucket tables (PK
    # = item_id alone, not tenant_id-widened) and the tests below reuse
    # item_id=1/2 across different tenants - see pg_helpers.wipe_tables.
    pg_helpers.wipe_tables("character_blueprints", "corp_blueprints", "character_assets", "corp_assets")
    yield


def _bp_row(item_id, type_id, me, te, runs, location_id=1, location_flag="Hangar", quantity=1):
    return (item_id, type_id, location_id, location_flag, quantity, me, te, runs)


def test_get_owned_bpo_best_me_te_none_when_not_owned(tenant):
    assert storage.get_owned_bpo_best_me_te(TYPE_ID) is None


def test_get_owned_bpo_best_me_te_ignores_bpcs(tenant):
    storage.replace_blueprints("character_blueprints", [
        _bp_row(1, TYPE_ID, me=10, te=20, runs=5),  # BPC (runs > -1) - must be ignored
    ])

    assert storage.get_owned_bpo_best_me_te(TYPE_ID) is None


def test_get_owned_bpo_best_me_te_picks_best_across_copies_and_tables(tenant):
    storage.replace_blueprints("character_blueprints", [
        _bp_row(1, TYPE_ID, me=4, te=10, runs=-1),
    ])
    storage.replace_blueprints("corp_blueprints", [
        _bp_row(2, TYPE_ID, me=10, te=6, runs=-1),  # better ME, worse TE - best of each wins independently
    ])

    assert storage.get_owned_bpo_best_me_te(TYPE_ID) == (10, 10)


def test_replace_blueprints_resolves_location_through_a_container(tenant):
    # GitHub issue #20: a BPC sitting inside a container (inside the corp
    # Office) used to be invisible to invention_logistics, since its own
    # location_id pointed at the container's item_id, not the outer
    # structure - it still showed up correctly on the Blueprints sheet
    # (which reads location_id unresolved) so the mismatch looked like a
    # "missing blueprint" bug specifically on the Logistics tab. A
    # blueprint's immediate container is always a regular asset, so
    # replace_blueprints resolves against the matching asset table
    # (corp_blueprints -> corp_assets here) instead of walking blueprint
    # rows against each other.
    office_item_id = 900
    container_item_id = 901
    storage.replace_assets("corp_assets", [
        (office_item_id, storage.OFFICE_TYPE_ID, LOCATION_ID, "OfficeFolder", 1, 0, "My Corp (corp)"),
        (container_item_id, 649, office_item_id, "CorpSAG1", 1, 0, "My Corp (corp)"),
    ])
    storage.replace_blueprints("corp_blueprints", [
        _bp_row(2, TYPE_ID, me=0, te=0, runs=3, location_id=container_item_id, location_flag="Unlocked"),
    ])

    with storage.connect() as conn:
        row = conn.execute(
            "SELECT resolved_location_id FROM corp_blueprints WHERE item_id = ?", (2,)
        ).fetchone()
    assert row[0] == LOCATION_ID


def test_replace_blueprints_without_matching_asset_row_keeps_own_location(tenant):
    # No corresponding character_assets row for the container - falls back
    # to the blueprint's own (unwrapped) location_id rather than erroring or
    # losing the row, matching every other test in this file that calls
    # replace_blueprints standalone.
    storage.replace_blueprints("character_blueprints", [
        _bp_row(1, TYPE_ID, me=0, te=0, runs=-2, location_id=LOCATION_ID),
    ])

    with storage.connect() as conn:
        row = conn.execute(
            "SELECT resolved_location_id FROM character_blueprints WHERE item_id = ?", (1,)
        ).fetchone()
    assert row[0] == LOCATION_ID

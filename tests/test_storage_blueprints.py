import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TYPE_ID = 999


@pytest.fixture(autouse=True)
def _wipe():
    # character_blueprints/corp_blueprints are column-only-bucket tables (PK
    # = item_id alone, not tenant_id-widened) and the tests below reuse
    # item_id=1/2 across different tenants - see pg_helpers.wipe_tables.
    pg_helpers.wipe_tables("character_blueprints", "corp_blueprints")
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

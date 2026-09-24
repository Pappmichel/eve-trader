import pytest

from eve_trader import storage
from eve_trader.production import actions


@pytest.fixture(autouse=True)
def _stub_shared_production_owner_ids(monkeypatch):
    # Known gap 3 (docs/ESI_ACCESS_PLAN.md): do_list_owned_blueprints
    # resolves sharing via shared_production_owner_ids (storage.connect(),
    # real Postgres) before calling storage.load_owned_blueprints - both
    # tests here monkeypatch that directly and have no tenant/Postgres
    # context, so stub the resolver to (None, None) ("unfiltered",
    # storage.load_owned_blueprints' own default). actions.py imports the
    # name directly (`from .engine import shared_production_owner_ids`),
    # so the patch target is actions.shared_production_owner_ids, not
    # engine.shared_production_owner_ids - a separate binding.
    monkeypatch.setattr(actions, "shared_production_owner_ids", lambda data_kind: (None, None))


@pytest.fixture(autouse=True)
def _no_manual_blueprints_by_default(monkeypatch):
    # docs/MANUAL_TRACKING_PLAN.md phase 5: do_list_owned_blueprints now
    # also appends storage.load_manual_owned_blueprints() - default to
    # none, same no-real-Postgres reasoning as the fixture above.
    monkeypatch.setattr(storage, "load_manual_owned_blueprints", lambda: [])


def test_groups_identical_specs_and_labels_bpo_vs_bpc(monkeypatch):
    monkeypatch.setattr(storage, "load_owned_blueprints", lambda **kwargs: [
        (100, -1, 10, 20, -1),   # BPO, ME10/TE20
        (100, -1, 10, 20, -1),   # another identical BPO -> grouped with the above
        (100, -1, 4, 8, 5),      # BPC, ME4/TE8, 5 runs remaining
        (200, 1, 0, 0, -1),      # BPO of a different type
    ])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: {
        100: (100, 0, "Rifter Blueprint", 0, 1, None, None),
        200: (200, 0, "Punisher Blueprint", 0, 1, None, None),
    }.get(type_id))

    result = actions.do_list_owned_blueprints()["rows"]
    by_key = {(r.type_id, r.is_original, r.runs): r for r in result}

    bpo = by_key[(100, True, None)]
    assert bpo.quantity == 2
    assert bpo.material_efficiency == 10
    assert bpo.time_efficiency == 20
    assert bpo.type_name == "Rifter Blueprint"

    bpc = by_key[(100, False, 5)]
    assert bpc.quantity == 1
    assert bpc.material_efficiency == 4
    assert bpc.time_efficiency == 8

    other = by_key[(200, True, None)]
    assert other.quantity == 1
    assert other.type_name == "Punisher Blueprint"


def test_unknown_type_falls_back_to_type_id_as_name(monkeypatch):
    monkeypatch.setattr(storage, "load_owned_blueprints", lambda **kwargs: [(999, -1, 10, 20, -1)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)

    rows = actions.do_list_owned_blueprints()["rows"]
    assert rows[0].type_name == "999"


def test_manual_rows_are_appended_with_source_and_never_merged(monkeypatch):
    monkeypatch.setattr(storage, "load_owned_blueprints", lambda **kwargs: [(100, -1, 10, 20, -1)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: {
        100: (100, 0, "Rifter Blueprint", 0, 1, None, None),
    }.get(type_id))
    monkeypatch.setattr(storage, "load_manual_owned_blueprints", lambda: [
        (7, 100, "Rifter Blueprint", True, 10, 20, None, 1, 1000000000001),
    ])

    result = actions.do_list_owned_blueprints()["rows"]

    esi_rows = [r for r in result if r.source == "esi"]
    manual_rows = [r for r in result if r.source == "manual"]
    assert len(esi_rows) == 1
    assert esi_rows[0].manual_id is None
    assert esi_rows[0].location_id is None
    assert len(manual_rows) == 1
    assert manual_rows[0].manual_id == 7
    assert manual_rows[0].location_id == 1000000000001
    assert manual_rows[0].type_name == "Rifter Blueprint"

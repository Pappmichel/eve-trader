from eve_trader import storage
from eve_trader.production import actions


def test_groups_identical_specs_and_labels_bpo_vs_bpc(monkeypatch):
    monkeypatch.setattr(storage, "load_owned_blueprints", lambda: [
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
    monkeypatch.setattr(storage, "load_owned_blueprints", lambda: [(999, -1, 10, 20, -1)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)

    rows = actions.do_list_owned_blueprints()["rows"]
    assert rows[0].type_name == "999"

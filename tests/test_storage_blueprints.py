from eve_trader import storage

TYPE_ID = 999


def _bp_row(item_id, type_id, me, te, runs, location_id=1, location_flag="Hangar", quantity=1):
    return (item_id, type_id, location_id, location_flag, quantity, me, te, runs)


def test_get_owned_bpo_best_me_te_none_when_not_owned(tmp_path):
    db_path = tmp_path / "test.db"
    assert storage.get_owned_bpo_best_me_te(TYPE_ID, db_path=db_path) is None


def test_get_owned_bpo_best_me_te_ignores_bpcs(tmp_path):
    db_path = tmp_path / "test.db"
    storage.replace_blueprints("character_blueprints", [
        _bp_row(1, TYPE_ID, me=10, te=20, runs=5),  # BPC (runs > -1) - must be ignored
    ], db_path=db_path)

    assert storage.get_owned_bpo_best_me_te(TYPE_ID, db_path=db_path) is None


def test_get_owned_bpo_best_me_te_picks_best_across_copies_and_tables(tmp_path):
    db_path = tmp_path / "test.db"
    storage.replace_blueprints("character_blueprints", [
        _bp_row(1, TYPE_ID, me=4, te=10, runs=-1),
    ], db_path=db_path)
    storage.replace_blueprints("corp_blueprints", [
        _bp_row(2, TYPE_ID, me=10, te=6, runs=-1),  # better ME, worse TE - best of each wins independently
    ], db_path=db_path)

    assert storage.get_owned_bpo_best_me_te(TYPE_ID, db_path=db_path) == (10, 10)

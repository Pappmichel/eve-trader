"""Tests for doctrine/actions.py's do_* orchestration functions that don't
need real Postgres - specifically do_list_contracts' source-name/hull
enrichment (engine.py's own contract_rows_from_db and storage.py stay
mocked out, same "thin wrapper, real logic elsewhere" split every other
do_* function in this app follows)."""
from eve_trader import storage
from eve_trader.doctrine import actions, esi_sync


def _contract_db_row(**overrides) -> tuple:
    base = dict(contract_id=1, source_role="doctrine:1560510246", for_corporation=False, issuer_id=42,
                start_location_id=1, status="outstanding", title="t", price=1.0, date_expired=None,
                matched_fitting_id="f1", match_score=1.0, validation_status="valid", synced_at="2026-01-01")
    base.update(overrides)
    fields = ("contract_id", "source_role", "for_corporation", "issuer_id", "start_location_id", "status", "title",
               "price", "date_expired", "matched_fitting_id", "match_score", "validation_status", "synced_at")
    return tuple(base[f] for f in fields)


def _fitting_db_row(**overrides) -> tuple:
    base = dict(fitting_id="f1", doctrine_id="d1", name="Fit", variant_label=None, hull_type_id=1000,
                raw_eft="", contract_target=1, stockpile_target=0, cargo_tolerance_pct=None, active=True,
                created_at=None, updated_at=None, fuel_bay_text=None, ship_maintenance_bay_text=None)
    base.update(overrides)
    fields = ("fitting_id", "doctrine_id", "name", "variant_label", "hull_type_id", "raw_eft", "contract_target",
               "stockpile_target", "cargo_tolerance_pct", "active", "created_at", "updated_at",
               "fuel_bay_text", "ship_maintenance_bay_text")
    return tuple(base[f] for f in fields)


def test_do_list_contracts_resolves_source_role_to_character_name(monkeypatch):
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **kwargs: [_contract_db_row()])
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [])
    monkeypatch.setattr(esi_sync, "list_doctrine_characters",
                         lambda: [("doctrine:1560510246", 1560510246, "pappmichl")])

    result = actions.do_list_contracts()

    assert result["rows"][0]["source_character_name"] == "pappmichl"
    assert result["rows"][0]["source_role"] == "doctrine:1560510246"  # raw key still present too


def test_do_list_contracts_falls_back_to_raw_role_key_when_character_unknown(monkeypatch):
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **kwargs: [_contract_db_row()])
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [])
    monkeypatch.setattr(esi_sync, "list_doctrine_characters", lambda: [])  # character since removed

    result = actions.do_list_contracts()

    assert result["rows"][0]["source_character_name"] is None


def test_do_list_contracts_resolves_hull_from_matched_fitting(monkeypatch):
    monkeypatch.setattr(storage, "list_doctrine_contracts",
                         lambda **kwargs: [_contract_db_row(matched_fitting_id="f1")])
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [_fitting_db_row(fitting_id="f1", hull_type_id=1000)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Rifter", 20.0, 1, 1, 0, None))
    monkeypatch.setattr(esi_sync, "list_doctrine_characters", lambda: [])

    result = actions.do_list_contracts()

    assert result["rows"][0]["hull_type_id"] == 1000
    assert result["rows"][0]["hull_name"] == "Rifter"


def test_do_list_contracts_blank_hull_when_unmatched(monkeypatch):
    monkeypatch.setattr(storage, "list_doctrine_contracts",
                         lambda **kwargs: [_contract_db_row(matched_fitting_id=None)])
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [_fitting_db_row(fitting_id="f1")])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Rifter", 20.0, 1, 1, 0, None))
    monkeypatch.setattr(esi_sync, "list_doctrine_characters", lambda: [])

    result = actions.do_list_contracts()

    assert result["rows"][0]["hull_type_id"] is None
    assert result["rows"][0]["hull_name"] is None


def _history_db_row(**overrides) -> tuple:
    base = dict(contract_id=1, source_role="doctrine:1560510246", fitting_id="f1", fitting_name="Fit",
                hull_type_id=1000, title="t", price=100.0, acceptor_id=99, acceptor_name=None,
                date_issued="2026-01-01T00:00:00Z", date_completed="2026-01-02T00:00:00Z")
    base.update(overrides)
    fields = ("contract_id", "source_role", "fitting_id", "fitting_name", "hull_type_id", "title", "price",
              "acceptor_id", "acceptor_name", "date_issued", "date_completed")
    return tuple(base[f] for f in fields)


def test_do_contract_history_resolves_hull_name_and_character_name(monkeypatch):
    # GitHub issue #19: fitting_name/hull_type_id are already denormalized
    # on the stored row (captured at record time in esi_sync.py, see
    # doctrine_contract_history's own schema comment) - only hull_name and
    # source_character_name need resolving here.
    monkeypatch.setattr(storage, "load_doctrine_contract_history", lambda: [_history_db_row()])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Rifter", 20.0, 1, 1, 0, None))
    monkeypatch.setattr(esi_sync, "list_doctrine_characters",
                         lambda: [("doctrine:1560510246", 1560510246, "pappmichl")])

    result = actions.do_contract_history()

    row = result["rows"][0]
    assert row["hull_type_id"] == 1000
    assert row["hull_name"] == "Rifter"
    assert row["source_character_name"] == "pappmichl"
    assert row["fitting_name"] == "Fit"  # already on the row, passed through untouched
    assert row["acceptor_id"] == 99


def test_do_contract_history_converts_real_datetime_columns_to_iso_strings(monkeypatch):
    # Confirmed real bug caught via live verification: storage.load_doctrine_
    # contract_history returns date_issued/date_completed as tz-aware
    # datetime objects (TIMESTAMPTZ columns), not strings - the API layer's
    # ContractHistoryRow schema declares these Optional[str], and FastAPI's
    # response validation 500s outright on a raw datetime rather than
    # silently stringifying it. do_contract_history must normalize both to
    # ISO strings before they ever reach the schema layer.
    import datetime as dt
    monkeypatch.setattr(storage, "load_doctrine_contract_history", lambda: [_history_db_row(
        fitting_id=None, fitting_name=None, hull_type_id=None,  # skip hull resolution - not this test's concern
        date_issued=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
        date_completed=dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc),
    )])
    monkeypatch.setattr(esi_sync, "list_doctrine_characters", lambda: [])

    result = actions.do_contract_history()

    row = result["rows"][0]
    assert row["date_issued"] == "2026-08-01T00:00:00+00:00"
    assert row["date_completed"] == "2026-08-02T00:00:00+00:00"


def test_do_contract_history_blank_hull_when_fitting_unknown(monkeypatch):
    # A history row can have no fitting_id at all (never matched while it
    # was still outstanding) - must not crash trying to resolve a hull name
    # for it.
    monkeypatch.setattr(storage, "load_doctrine_contract_history",
                         lambda: [_history_db_row(fitting_id=None, fitting_name=None, hull_type_id=None)])
    monkeypatch.setattr(esi_sync, "list_doctrine_characters", lambda: [])

    result = actions.do_contract_history()

    row = result["rows"][0]
    assert row["hull_type_id"] is None
    assert row["hull_name"] is None


def test_do_get_shopping_list_wraps_engine_result(monkeypatch):
    from eve_trader.doctrine import engine
    from eve_trader.doctrine.models import ShoppingListRow

    monkeypatch.setattr(engine, "shopping_list_rows", lambda doctrine_id, cfg: [
        ShoppingListRow(type_id=100, type_name="Widget", shortfall=5.0, build_cost=10.0, cj_price=12.0,
                         jita_landed_price=15.0, recommended_source="Build", total_cost=50.0),
    ])

    result = actions.do_get_shopping_list()

    assert result["rows"] == [{
        "type_id": 100, "type_name": "Widget", "shortfall": 5.0, "build_cost": 10.0, "cj_price": 12.0,
        "jita_landed_price": 15.0, "recommended_source": "Build", "total_cost": 50.0,
    }]

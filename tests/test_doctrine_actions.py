"""Tests for doctrine/actions.py's do_* orchestration functions that don't
need real Postgres - specifically do_list_contracts' source-name/hull
enrichment (engine.py's own contract_rows_from_db and storage.py stay
mocked out, same "thin wrapper, real logic elsewhere" split every other
do_* function in this app follows)."""
from contextlib import contextmanager

from eve_trader import storage
from eve_trader.doctrine import actions, engine, esi_sync
from eve_trader.doctrine.models import ParsedFitting


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


def test_do_contract_history_converts_real_uuid_fitting_id_to_string(monkeypatch):
    # Confirmed real bug caught live (2026-08-24): storage.load_doctrine_
    # contract_history returns fitting_id as a real UUID object (it's a UUID
    # column, see docs/doctrine_schema.sql), not a string - same class of bug
    # as the date_issued/date_completed one above, and just as fatal: the API
    # layer's ContractHistoryRow schema declares fitting_id Optional[str], so
    # FastAPI's response validation 500s on a raw UUID rather than silently
    # stringifying it.
    import uuid
    fitting_uuid = uuid.UUID("516e466c-9ec3-4d4f-a8b1-bc87e8b41ed7")
    monkeypatch.setattr(storage, "load_doctrine_contract_history",
                         lambda: [_history_db_row(fitting_id=fitting_uuid, hull_type_id=None)])
    monkeypatch.setattr(esi_sync, "list_doctrine_characters", lambda: [])

    result = actions.do_contract_history()

    assert result["rows"][0]["fitting_id"] == "516e466c-9ec3-4d4f-a8b1-bc87e8b41ed7"


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


@contextmanager
def _batch():
    yield


def _stub_fitting_update(monkeypatch, fitting_row=None):
    row = fitting_row or _fitting_db_row()
    fitting = engine.fitting_from_row(row)
    monkeypatch.setattr(storage, "get_fitting", lambda fid: row)
    monkeypatch.setattr(storage, "batch_session", _batch)
    monkeypatch.setattr(storage, "update_fitting", lambda *a, **k: None)
    monkeypatch.setattr(storage, "replace_fitting_items", lambda *a, **k: None)
    monkeypatch.setattr(storage, "replace_fitting_parse_issues", lambda *a, **k: None)
    monkeypatch.setattr(engine, "load_fitting_with_items", lambda fid: (fitting, []))
    monkeypatch.setattr(engine, "parse_fitting_text",
                         lambda text: ParsedFitting(hull_type_id=fitting.hull_type_id,
                                                     hull_name="Hull", fit_name=fitting.name))
    monkeypatch.setattr(actions, "_merge_bay_items", lambda parsed, fuel, smb: ([], []))
    return fitting


def test_do_update_fitting_stockpile_target_skips_contract_validation(monkeypatch):
    _stub_fitting_update(monkeypatch)
    match_ids = []
    monkeypatch.setattr(engine, "match_and_validate_contract",
                         lambda *a, **k: match_ids.append(a[0]) or (None, 0.0, [], "unmatched"))
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [
        _contract_db_row(contract_id=1, matched_fitting_id="f1"),
        _contract_db_row(contract_id=2, matched_fitting_id="f2"),
    ])

    actions.do_update_fitting("f1", stockpile_target=9)

    assert match_ids == []


def test_do_update_fitting_eft_revalidates_own_and_unmatched_not_other_hull(monkeypatch):
    _stub_fitting_update(monkeypatch, _fitting_db_row(fitting_id="f1", hull_type_id=1000))
    match_ids = []

    def fake_match(contract_id, title, items, candidates, cfg=None):
        match_ids.append(contract_id)
        return ("f1", 1.0, [], "valid")

    monkeypatch.setattr(engine, "match_and_validate_contract", fake_match)
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [])
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [
        _contract_db_row(contract_id=1, matched_fitting_id="f1"),
        _contract_db_row(contract_id=2, matched_fitting_id="f2"),
        _contract_db_row(contract_id=3, matched_fitting_id=None),
    ])
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [
        _fitting_db_row(fitting_id="f1", hull_type_id=1000),
        _fitting_db_row(fitting_id="f2", hull_type_id=2000),
    ])
    monkeypatch.setattr(storage, "load_doctrine_contract_items", lambda cid: [])
    monkeypatch.setattr(storage, "load_doctrine_contract_deviations", lambda cid: [])
    monkeypatch.setattr(storage, "replace_doctrine_sync_snapshot", lambda *a, **k: None)

    actions.do_update_fitting("f1", raw_eft="[Rifter, Fit]\n")

    assert match_ids == [1, 3]


def test_do_update_fitting_eft_still_rechecks_unmatched_contracts(monkeypatch):
    # Regression: an EFT/soll change must still try to match contracts that
    # were unmatched, not only rows already assigned to this fitting.
    _stub_fitting_update(monkeypatch, _fitting_db_row(fitting_id="f1", hull_type_id=1000))
    match_ids = []
    monkeypatch.setattr(engine, "match_and_validate_contract",
                         lambda contract_id, *a, **k: match_ids.append(contract_id) or (None, 0.0, [], "unmatched"))
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [])
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [
        _contract_db_row(contract_id=99, matched_fitting_id=None),
    ])
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [
        _fitting_db_row(fitting_id="f1", hull_type_id=1000),
    ])
    monkeypatch.setattr(storage, "load_doctrine_contract_items", lambda cid: [])
    monkeypatch.setattr(storage, "load_doctrine_contract_deviations", lambda cid: [])
    monkeypatch.setattr(storage, "replace_doctrine_sync_snapshot", lambda *a, **k: None)

    actions.do_update_fitting("f1", raw_eft="[Rifter, Fit]\n")

    assert match_ids == [99]


def test_do_update_fitting_cargo_tolerance_only_revalidates_own_contracts(monkeypatch):
    _stub_fitting_update(monkeypatch, _fitting_db_row(fitting_id="f1", hull_type_id=1000))
    match_ids = []
    monkeypatch.setattr(engine, "match_and_validate_contract",
                         lambda contract_id, *a, **k: match_ids.append(contract_id) or ("f1", 1.0, [], "valid"))
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [])
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [
        _contract_db_row(contract_id=1, matched_fitting_id="f1"),
        _contract_db_row(contract_id=2, matched_fitting_id="f2"),
        _contract_db_row(contract_id=3, matched_fitting_id=None),
    ])
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [
        _fitting_db_row(fitting_id="f1", hull_type_id=1000),
        _fitting_db_row(fitting_id="f2", hull_type_id=1000),
    ])
    monkeypatch.setattr(storage, "load_doctrine_contract_items", lambda cid: [])
    monkeypatch.setattr(storage, "load_doctrine_contract_deviations", lambda cid: [])
    monkeypatch.setattr(storage, "replace_doctrine_sync_snapshot", lambda *a, **k: None)

    actions.do_update_fitting("f1", cargo_tolerance_pct=0.5)

    assert match_ids == [1]


def test_do_sync_contracts_emits_increasing_batch_progress(monkeypatch):
    """Track A: each finished contract-item fetch reports batch/total_batches."""
    from eve_trader.auth import TokenRecord
    from eve_trader.doctrine.config import DoctrineConfig

    structure_id = 1000000000001
    cfg = DoctrineConfig(doctrine_structure_id=structure_id)
    contracts = [
        {
            "contract_id": cid, "type": "item_exchange", "status": "outstanding",
            "start_location_id": structure_id, "issuer_id": 42, "issuer_corporation_id": 500,
            "title": f"c{cid}", "price": 1.0, "date_expired": None,
        }
        for cid in (101, 102, 103)
    ]

    class FakeTM:
        def __init__(self, *a, **k):
            pass

        def list_roles(self, prefix):
            return ["doctrine:42"]

        def get_record(self, role):
            return TokenRecord(
                role=role, character_id=42, character_name="Pilot",
                access_token="x", refresh_token="y", expires_at=9e9, scopes="",
            )

    class FakeClient:
        def __init__(self, tokens=None):
            pass

        def character_contracts(self, character_id, auth_role=None):
            return contracts

        def character_public_info(self, character_id):
            return {"corporation_id": 500}

        def corporation_contracts(self, corporation_id, auth_role=None):
            return []

        def character_contract_items(self, character_id, contract_id, auth_role=None):
            return [{"record_id": 1, "type_id": 1000, "quantity": 1,
                     "is_included": True, "is_singleton": True}]

        def resolve_names(self, ids):
            return {}

    monkeypatch.setattr(esi_sync, "TokenManager", FakeTM)
    monkeypatch.setattr(esi_sync, "ESIClient", FakeClient)
    monkeypatch.setattr(storage, "load_doctrine_contracts", lambda: [])
    monkeypatch.setattr(storage, "load_doctrine_contract_items", lambda cid: [])
    monkeypatch.setattr(storage, "with_current_tenant", lambda fn: fn)
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [])
    monkeypatch.setattr(
        engine, "match_and_validate_contract",
        lambda *a, **k: (None, 0.0, [], engine.NO_HULL_MATCH),
    )

    @contextmanager
    def _batch():
        yield

    monkeypatch.setattr(storage, "batch_session", _batch)
    monkeypatch.setattr(storage, "replace_doctrine_sync_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(storage, "upsert_doctrine_contract_history", lambda *a, **k: None)
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda *a, **k: None)

    seen = []
    # cfg passed explicitly: the function default is the live ConfigProxy
    # (tenant settings), which may have no structure_id in unit tests.
    esi_sync.sync_contracts(cfg=cfg, progress_callback=seen.append)

    assert [p["batch"] for p in seen] == [1, 2, 3]
    assert all(p["phase"] == "sync" for p in seen)
    assert all(p["total_batches"] == 3 for p in seen)
    assert all(p["message"] == "Fetching contract items" for p in seen)


def test_do_sync_contracts_forwards_progress_callback(monkeypatch):
    captured = {}

    def fake_sync(cfg=None, progress_callback=None):
        captured["cb"] = progress_callback
        return {"ok": True}

    monkeypatch.setattr(esi_sync, "sync_contracts", fake_sync)
    cb = object()
    assert actions.do_sync_contracts(progress_callback=cb) == {"ok": True}
    assert captured["cb"] is cb

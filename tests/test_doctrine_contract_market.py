"""Contract Market (GitHub issue #428): public + alliance scan, system
filter, match reuse, and never writing doctrine_contracts.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.doctrine import contract_market
from eve_trader.doctrine.config import DoctrineConfig

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

SYSTEM_ID = 30000142
REGION_ID = 10000002
STRUCTURE_IN_SYSTEM = 60003760
STRUCTURE_OTHER = 60000001
CONTRACT_PUBLIC = 900001
CONTRACT_ALLIANCE = 900002
FAKE_FITTING = "11111111-1111-1111-1111-111111111111"
CORP_ID = 98000001
ALLIANCE_ID = 99000001


@pytest.fixture
def cfg():
    return DoctrineConfig(
        contract_market_system_id=SYSTEM_ID,
        contract_market_include_public=True,
        contract_market_include_alliance=True,
    )


@pytest.fixture(autouse=True)
def _wipe_market(_apply_doctrine_schema, tenant):
    pg_helpers.wipe_tables(
        "doctrine_market_contract_items", "doctrine_market_contracts",
        "doctrine_market_scans", "doctrine_contract_items",
        "doctrine_contract_deviations", "doctrine_contracts",
    )
    yield


def _seed_system_and_station(system_id=SYSTEM_ID, station_id=STRUCTURE_IN_SYSTEM,
                             name="Jita", region_id=REGION_ID, station_name="Jita IV"):
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO sde_solar_systems (solar_system_id, solar_system_name, security, region_id) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (solar_system_id) DO UPDATE SET "
            "solar_system_name = excluded.solar_system_name, region_id = excluded.region_id",
            (system_id, name, 0.9, region_id),
        )
        conn.execute(
            "INSERT INTO sde_stations (station_id, solar_system_id, station_name) "
            "VALUES (%s,%s,%s) ON CONFLICT (station_id) DO UPDATE SET "
            "solar_system_id = excluded.solar_system_id, station_name = excluded.station_name",
            (station_id, system_id, station_name),
        )


FUTURE = "2099-01-08T00:00:00Z"


def _raw(**overrides):
    """A row of GET /contracts/public/{region}/ as ESI really sends it: no
    status and no availability (live response, 2026-11-06)."""
    base = {
        "collateral": 0.0,
        "contract_id": CONTRACT_PUBLIC,
        "date_expired": FUTURE,
        "date_issued": "2026-01-01T00:00:00Z",
        "days_to_complete": 0,
        "end_location_id": STRUCTURE_IN_SYSTEM,
        "issuer_corporation_id": 98000001,
        "issuer_id": 111,
        "price": 50_000_000.0,
        "reward": 0.0,
        "start_location_id": STRUCTURE_IN_SYSTEM,
        "title": "Doctrine Fit",
        "type": "item_exchange",
        "volume": 100.0,
    }
    base.update(overrides)
    return base


def _alliance_raw(**overrides):
    """A row of the corporation contract list. ESI says availability "personal"
    even for an alliance contract (esi-issues #1447); assignee_id identifies it."""
    return _raw(**{"status": "outstanding", "availability": "personal", "assignee_id": ALLIANCE_ID, **overrides})


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(contract_market, "ESIClient", lambda tokens=None: client)
    monkeypatch.setattr(contract_market, "TokenManager", lambda *a, **k: MagicMock())
    monkeypatch.setattr(contract_market.location_resolver, "resolve_ids", lambda ids, force=False: {})
    monkeypatch.setattr(contract_market, "shared_owner_ids", lambda *a, **k: [])
    monkeypatch.setattr(contract_market, "_min_contract_volume", lambda *a: 0.0)


# ------------------------------------------------------------- pure filters
def test_public_row_without_status_is_open():
    assert contract_market._is_open_public_item_exchange(_raw()) is True


def test_public_filter_rejects_courier_and_expired():
    assert contract_market._is_open_public_item_exchange(_raw(type="courier")) is False
    assert contract_market._is_open_public_item_exchange(
        _raw(date_expired="2020-01-01T00:00:00Z")) is False


def test_alliance_filter_goes_by_assignee_not_availability():
    is_open = contract_market._is_open_alliance_contract
    assert is_open(_alliance_raw(), ALLIANCE_ID) is True
    assert is_open(_alliance_raw(), 99000002) is False
    assert is_open(_alliance_raw(type="courier"), ALLIANCE_ID) is False
    assert is_open(_alliance_raw(date_expired="2020-01-01T00:00:00Z"), ALLIANCE_ID) is False
    assert is_open(_raw(status="finished", assignee_id=ALLIANCE_ID), ALLIANCE_ID) is False


def test_filter_by_system_keeps_only_target():
    contracts = [
        _raw(contract_id=1, start_location_id=STRUCTURE_IN_SYSTEM),
        _raw(contract_id=2, start_location_id=STRUCTURE_OTHER),
    ]
    system_of = {STRUCTURE_IN_SYSTEM: SYSTEM_ID, STRUCTURE_OTHER: 30000001}
    kept = contract_market._filter_by_system(contracts, SYSTEM_ID, system_of)
    assert [c["contract_id"] for c in kept] == [1]


def test_filter_by_system_drops_unresolvable_locations():
    contracts = [_raw(start_location_id=99999999)]
    assert contract_market._filter_by_system(contracts, SYSTEM_ID, {}) == []


# ------------------------------------------------------------- scan orchestration
def test_scan_requires_system(cfg):
    cfg.contract_market_system_id = None
    with pytest.raises(ActionError, match="solar system"):
        contract_market.scan_contract_market(cfg)


def test_scan_requires_a_source(cfg):
    cfg.contract_market_include_public = False
    cfg.contract_market_include_alliance = False
    with pytest.raises(ActionError, match="Public and/or Alliance"):
        contract_market.scan_contract_market(cfg)


def test_scan_public_and_alliance_match_writes_market_not_doctrine(monkeypatch, cfg):
    """Matched rows land in doctrine_market_* only; doctrine_contracts stays empty."""
    _seed_system_and_station()

    public_raw = [_raw(contract_id=CONTRACT_PUBLIC, title="Public Fit", price=40e6)]
    alliance_raw = [_alliance_raw(contract_id=CONTRACT_ALLIANCE, title="Alliance Fit", price=45e6)]
    items = [{"record_id": 1, "type_id": 587, "quantity": 1,
              "is_included": True, "is_singleton": True}]

    client = MagicMock()
    client.public_contracts.return_value = public_raw
    client.public_contract_items.return_value = items
    client.corporation_contracts.return_value = alliance_raw + [
        _alliance_raw(contract_id=900003, assignee_id=CORP_ID)]  # the corp's own, not the alliance's
    client.corporation_contract_items.return_value = items
    client.resolve_names.return_value = {111: "Seller One"}
    _patch_client(monkeypatch, client)

    monkeypatch.setattr(contract_market, "shared_owner_ids", lambda *a, **k: [CORP_ID])
    monkeypatch.setattr(contract_market, "_corp_reader", lambda *a: ("doctrine:1", ALLIANCE_ID))
    monkeypatch.setattr(contract_market.engine, "load_match_candidates", lambda: [object()])
    monkeypatch.setattr(
        contract_market.engine, "match_and_validate_contract",
        lambda *a, **k: (FAKE_FITTING, 1.0, [], "valid"),
    )

    result = contract_market.scan_contract_market(cfg)

    assert result["matched"] == 2
    assert result["public_candidates"] == 1
    assert result["alliance_candidates"] == 1
    assert result["system_id"] == SYSTEM_ID

    market_rows = storage.list_doctrine_market_contracts()
    assert len(market_rows) == 2
    assert {r[1] for r in market_rows} == {"public", "alliance"}
    assert all(str(r[11]) == FAKE_FITTING for r in market_rows)

    assert storage.list_doctrine_contracts() == []

    scan = storage.get_doctrine_market_scan()
    assert scan is not None
    assert scan[1] == SYSTEM_ID
    assert scan[6] == 2


def test_scan_skips_other_system_public_contracts(monkeypatch, cfg):
    _seed_system_and_station()
    _seed_system_and_station(system_id=30000001, station_id=STRUCTURE_OTHER,
                             name="Elsewhere", region_id=10000001, station_name="Elsewhere")

    client = MagicMock()
    client.public_contracts.return_value = [
        _raw(contract_id=1, start_location_id=STRUCTURE_OTHER),
        _raw(contract_id=2, start_location_id=STRUCTURE_IN_SYSTEM),
    ]
    client.public_contract_items.return_value = [
        {"record_id": 1, "type_id": 587, "quantity": 1, "is_included": True, "is_singleton": True},
    ]
    client.resolve_names.return_value = {111: "Seller"}
    _patch_client(monkeypatch, client)

    cfg.contract_market_include_alliance = False
    monkeypatch.setattr(contract_market.engine, "load_match_candidates", lambda: [object()])
    monkeypatch.setattr(
        contract_market.engine, "match_and_validate_contract",
        lambda *a, **k: (FAKE_FITTING, 1.0, [], "valid"),
    )

    result = contract_market.scan_contract_market(cfg)
    assert result["public_candidates"] == 1
    assert result["matched"] == 1
    rows = storage.list_doctrine_market_contracts()
    assert len(rows) == 1
    assert rows[0][0] == 2


def test_unmatched_contracts_are_not_stored(monkeypatch, cfg):
    _seed_system_and_station()

    client = MagicMock()
    client.public_contracts.return_value = [_raw()]
    client.public_contract_items.return_value = [
        {"record_id": 1, "type_id": 1, "quantity": 1, "is_included": True, "is_singleton": False},
    ]
    client.resolve_names.return_value = {}
    _patch_client(monkeypatch, client)

    cfg.contract_market_include_alliance = False
    monkeypatch.setattr(contract_market.engine, "load_match_candidates", lambda: [object()])
    monkeypatch.setattr(
        contract_market.engine, "match_and_validate_contract",
        lambda *a, **k: (None, 0.0, [], "no_relevant_hull"),
    )

    result = contract_market.scan_contract_market(cfg)
    assert result["matched"] == 0
    assert storage.list_doctrine_market_contracts() == []


def test_replace_snapshot_never_touches_doctrine_contracts():
    storage.replace_doctrine_sync_snapshot(
        [(99, "doctrine:1", False, 1, STRUCTURE_IN_SYSTEM, "outstanding", "Own",
          1.0, None, None, None, "unmatched", "2026-01-01T00:00:00Z")],
        [], [],
        owner_character_id=1, owner_name="doctrine:1",
    )
    assert len(storage.list_doctrine_contracts()) == 1

    storage.replace_doctrine_market_snapshot(
        [(CONTRACT_PUBLIC, "public", 111, "Seller", STRUCTURE_IN_SYSTEM, SYSTEM_ID,
          "outstanding", "Market", 1.0, None, None,
          FAKE_FITTING, 1.0, "valid", "2026-01-02T00:00:00Z")],
        [(CONTRACT_PUBLIC, 1, 587, 1.0, True, True)],
        scanned_at="2026-01-02T00:00:00Z", system_id=SYSTEM_ID,
        include_public=True, include_alliance=False,
        public_candidates=1, alliance_candidates=0, matched=1,
    )
    assert len(storage.list_doctrine_contracts()) == 1
    assert storage.list_doctrine_contracts()[0][0] == 99
    assert len(storage.list_doctrine_market_contracts()) == 1


def test_items_carried_forward_skips_esi(monkeypatch, cfg):
    _seed_system_and_station()

    storage.replace_doctrine_market_snapshot(
        [(CONTRACT_PUBLIC, "public", 111, "Seller", STRUCTURE_IN_SYSTEM, SYSTEM_ID,
          "outstanding", "Market", 1.0, None, None,
          FAKE_FITTING, 1.0, "valid", "2026-01-01T00:00:00Z")],
        [(CONTRACT_PUBLIC, 1, 587, 1.0, True, True)],
        scanned_at="2026-01-01T00:00:00Z", system_id=SYSTEM_ID,
        include_public=True, include_alliance=False,
        public_candidates=1, alliance_candidates=0, matched=1,
    )

    client = MagicMock()
    client.public_contracts.return_value = [_raw()]
    client.resolve_names.return_value = {111: "Seller"}
    client.public_contract_items.side_effect = AssertionError("items should be carried")
    _patch_client(monkeypatch, client)

    cfg.contract_market_include_alliance = False
    monkeypatch.setattr(contract_market.engine, "load_match_candidates", lambda: [object()])
    monkeypatch.setattr(
        contract_market.engine, "match_and_validate_contract",
        lambda *a, **k: (FAKE_FITTING, 1.0, [], "valid"),
    )

    result = contract_market.scan_contract_market(cfg)
    assert result["matched"] == 1
    client.public_contract_items.assert_not_called()


# ------------------------------------------------------------- review of #432
ITEM = {"record_id": 1, "type_id": 587, "quantity": 1, "is_included": True, "is_singleton": True}


def _scan_setup(monkeypatch, cfg, client, *, status="valid", fitting=FAKE_FITTING):
    _seed_system_and_station()
    client.resolve_names.return_value = {111: "Seller"}
    _patch_client(monkeypatch, client)
    cfg.contract_market_include_alliance = False
    monkeypatch.setattr(contract_market.engine, "load_match_candidates", lambda: [object()])
    monkeypatch.setattr(contract_market.engine, "match_and_validate_contract",
                        lambda *a, **k: (fitting, 1.0, [], status))


def _seed_previous_scan():
    storage.replace_doctrine_market_snapshot(
        [(CONTRACT_PUBLIC, "public", 111, "Seller", STRUCTURE_IN_SYSTEM, SYSTEM_ID,
          "outstanding", "Market", 1.0, None, FUTURE,
          FAKE_FITTING, 1.0, "valid", "2026-01-01T00:00:00Z")],
        [(CONTRACT_PUBLIC, 1, 587, 1.0, True, True)],
        scanned_at="2026-01-01T00:00:00Z", system_id=SYSTEM_ID,
        include_public=True, include_alliance=False,
        public_candidates=1, alliance_candidates=0, matched=1,
    )


def test_public_scan_needs_no_status_field(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = [_raw()]
    client.public_contract_items.return_value = [ITEM]
    _scan_setup(monkeypatch, cfg, client)
    result = contract_market.scan_contract_market(cfg)
    assert result["public_candidates"] == 1
    assert result["matched"] == 1


def test_unknown_structures_are_counted_not_looked_up(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = [_raw(start_location_id=1_049_000_000_000)]
    _scan_setup(monkeypatch, cfg, client)
    looked_up = []
    monkeypatch.setattr(contract_market.location_resolver, "resolve_ids",
                        lambda ids, force=False: looked_up.extend(ids) or {})
    result = contract_market.scan_contract_market(cfg)
    assert result["unknown_locations"] == 1
    assert result["public_candidates"] == 0
    assert looked_up == []
    assert storage.get_doctrine_market_scan()[7] == 1


def test_items_of_unmatched_contracts_are_kept_for_the_next_scan(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = [_raw()]
    client.public_contract_items.return_value = [ITEM]
    _scan_setup(monkeypatch, cfg, client, fitting=None, status="no_relevant_hull")
    contract_market.scan_contract_market(cfg)
    assert client.public_contract_items.call_count == 1
    contract_market.scan_contract_market(cfg)
    assert client.public_contract_items.call_count == 1


def test_new_item_reads_stop_at_the_cap(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = [_raw(contract_id=i) for i in range(1, 6)]
    client.public_contract_items.return_value = [ITEM]
    _scan_setup(monkeypatch, cfg, client)
    monkeypatch.setattr(contract_market, "MAX_NEW_ITEM_FETCHES", 2)
    assert contract_market.scan_contract_market(cfg)["items_pending"] == 3
    assert client.public_contract_items.call_count == 2
    assert contract_market.scan_contract_market(cfg)["items_pending"] == 1


def test_empty_list_for_a_region_with_contracts_keeps_the_last_result(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = []
    _scan_setup(monkeypatch, cfg, client)
    _seed_previous_scan()
    with pytest.raises(ActionError, match="last result was kept"):
        contract_market.scan_contract_market(cfg)
    assert len(storage.list_doctrine_market_contracts()) == 1


def test_failed_item_reads_keep_the_last_result(monkeypatch, cfg):
    from eve_trader.esi_client import ESIError
    client = MagicMock()
    client.public_contracts.return_value = [_raw(contract_id=i) for i in range(10, 13)]
    client.public_contract_items.side_effect = ESIError("503")
    _scan_setup(monkeypatch, cfg, client)
    _seed_previous_scan()
    with pytest.raises(ActionError, match="last result was kept"):
        contract_market.scan_contract_market(cfg)
    assert len(storage.list_doctrine_market_contracts()) == 1


def test_all_alliance_reads_failing_keeps_the_last_result(monkeypatch, cfg):
    from eve_trader.esi_client import ESIError
    client = MagicMock()
    client.public_contracts.return_value = []
    client.corporation_contracts.side_effect = ESIError("503")
    _scan_setup(monkeypatch, cfg, client)
    cfg.contract_market_include_public = False
    cfg.contract_market_include_alliance = True
    monkeypatch.setattr(contract_market, "shared_owner_ids", lambda *a, **k: [CORP_ID])
    monkeypatch.setattr(contract_market, "_corp_reader", lambda *a: ("doctrine:1", ALLIANCE_ID))
    _seed_previous_scan()
    with pytest.raises(ActionError, match="last result was kept"):
        contract_market.scan_contract_market(cfg)
    assert len(storage.list_doctrine_market_contracts()) == 1


def test_contract_asking_for_items_is_not_listed(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = [_raw(price=1.0)]
    client.public_contract_items.return_value = [
        ITEM, {"record_id": 2, "type_id": 44992, "quantity": 500, "is_included": False, "is_singleton": False},
    ]
    _scan_setup(monkeypatch, cfg, client)
    result = contract_market.scan_contract_market(cfg)
    assert result["asks_for_items"] == 1
    assert result["matched"] == 0


def test_invalid_fit_is_not_listed(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = [_raw()]
    client.public_contract_items.return_value = [ITEM]
    _scan_setup(monkeypatch, cfg, client, status="invalid")
    assert contract_market.scan_contract_market(cfg)["matched"] == 0


def test_contract_smaller_than_any_hull_is_never_read(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = [_raw(contract_id=1, volume=5.0), _raw(contract_id=2, volume=2500.0)]
    client.public_contract_items.return_value = [ITEM]
    _scan_setup(monkeypatch, cfg, client)
    monkeypatch.setattr(contract_market, "_min_contract_volume", lambda *a: 2250.0)
    result = contract_market.scan_contract_market(cfg)
    assert result["public_candidates"] == 1
    client.public_contract_items.assert_called_once_with(2)


class _Hull:
    def __init__(self, hull):
        self.fitting = type("F", (), {"hull_type_id": hull})


def test_min_volume_is_the_smallest_packaged_hull(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(storage, "get_cached_packaged_volume", {587: 2500.0, 24698: 15000.0}.get)
    assert contract_market._min_contract_volume([_Hull(587), _Hull(24698)], MagicMock()) == 2250.0


def test_unknown_hull_volume_turns_the_filter_off(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_packaged_volume", lambda t: None)
    client = MagicMock()
    client.get_packaged_volume.return_value = None
    assert contract_market._min_contract_volume([_Hull(587)], client) == 0.0


def test_two_corps_of_one_alliance_read_the_list_once(monkeypatch, cfg):
    client = MagicMock()
    client.corporation_contracts.return_value = [_alliance_raw()]
    client.corporation_contract_items.return_value = [ITEM]
    _scan_setup(monkeypatch, cfg, client)
    cfg.contract_market_include_public = False
    cfg.contract_market_include_alliance = True
    monkeypatch.setattr(contract_market, "shared_owner_ids", lambda *a, **k: [CORP_ID, CORP_ID + 1])
    monkeypatch.setattr(contract_market, "_corp_reader", lambda *a: ("doctrine:1", ALLIANCE_ID))
    assert contract_market.scan_contract_market(cfg)["matched"] == 1
    assert client.corporation_contracts.call_count == 1


def test_public_scan_runs_without_a_sharing_character(monkeypatch, cfg):
    client = MagicMock()
    client.public_contracts.return_value = [_raw()]
    client.public_contract_items.return_value = [ITEM]
    _scan_setup(monkeypatch, cfg, client)
    cfg.contract_market_include_alliance = True
    result = contract_market.scan_contract_market(cfg)
    assert result["alliance_skipped"] is True
    assert result["matched"] == 1


def test_alliance_only_without_a_sharing_character_fails_before_esi(monkeypatch, cfg):
    client = MagicMock()
    _scan_setup(monkeypatch, cfg, client)
    cfg.contract_market_include_public = False
    cfg.contract_market_include_alliance = True
    with pytest.raises(ActionError, match="sharing Contracts"):
        contract_market.scan_contract_market(cfg)
    client.public_contracts.assert_not_called()


def test_rescan_right_away_is_rejected():
    storage.replace_doctrine_market_snapshot(
        [], [], scanned_at=contract_market._now_iso(), system_id=SYSTEM_ID,
        include_public=True, include_alliance=False,
        public_candidates=0, alliance_candidates=0, matched=0,
    )
    with pytest.raises(ActionError, match="scanned"):
        contract_market.reject_scan_too_soon(SYSTEM_ID)
    contract_market.reject_scan_too_soon(30000001)


class _Fit:
    def __init__(self, fitting_id):
        self.fitting_id = fitting_id


class _Cand:
    def __init__(self, fitting_id):
        self.fitting = _Fit(fitting_id)


def _results(monkeypatch, *, shared=True, expired=False):
    other = "22222222-2222-2222-2222-222222222222"
    _seed_system_and_station()
    storage.replace_doctrine_market_snapshot(
        [(CONTRACT_PUBLIC, "public", 111, "Seller", STRUCTURE_IN_SYSTEM, SYSTEM_ID,
          "outstanding", "Market", 10.0, None, "2020-01-01T00:00:00Z" if expired else FUTURE,
          FAKE_FITTING, 1.0, "valid", "2026-01-01T00:00:00Z"),
         (CONTRACT_ALLIANCE, "alliance", 111, "Seller", STRUCTURE_IN_SYSTEM, SYSTEM_ID,
          "outstanding", "Own", 20.0, None, FUTURE,
          FAKE_FITTING, 1.0, "tolerable", "2026-01-01T00:00:00Z")],
        [], scanned_at="2026-01-01T00:00:00Z", system_id=SYSTEM_ID,
        include_public=True, include_alliance=True,
        public_candidates=1, alliance_candidates=1, matched=2, unknown_locations=3,
    )
    monkeypatch.setattr(contract_market, "shared_owner_ids", lambda *a, **k: [1] if shared else [])
    monkeypatch.setattr(contract_market.engine, "load_match_candidates",
                        lambda: [_Cand(FAKE_FITTING), _Cand(other)])
    monkeypatch.setattr(contract_market.engine, "_fit_identity", lambda c: "same")
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [
        (FAKE_FITTING, "d1", "Fit A", None, 587), (other, "d2", "Fit B", None, 587)])
    monkeypatch.setattr(storage, "list_doctrines", lambda: [("d1", "Alpha", None, True), ("d2", "Beta", None, True)])
    monkeypatch.setattr(storage, "get_sde_type", lambda t: (t, 1, "Rifter"))
    return contract_market.market_results(DoctrineConfig())


def test_results_list_identical_fits_under_every_doctrine(monkeypatch):
    out = _results(monkeypatch)
    assert [d["doctrine_name"] for d in out["doctrines"]] == ["Alpha", "Beta"]
    for doctrine in out["doctrines"]:
        fitting = doctrine["fittings"][0]
        assert fitting["count"] == 2
        assert fitting["min_price"] == 10.0
        assert fitting["avg_price"] == 15.0
    assert out["scan"]["unknown_locations"] == 3


def test_results_hide_expired_contracts(monkeypatch):
    out = _results(monkeypatch, expired=True)
    assert out["doctrines"][0]["fittings"][0]["count"] == 1


def test_results_hide_alliance_rows_after_sharing_ends(monkeypatch):
    out = _results(monkeypatch, shared=False)
    sources = {c["source"] for f in out["doctrines"][0]["fittings"] for c in f["contracts"]}
    assert sources == {"public"}


def test_app_role_cannot_read_another_tenants_market_rows():
    _seed_previous_scan()
    other = "99999999-9999-9999-9999-999999999999"
    with storage.tenant_context(other):
        assert storage.list_doctrine_market_contracts() == []
        assert storage.get_doctrine_market_scan() is None

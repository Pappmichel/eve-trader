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


def _raw(**overrides):
    base = {
        "contract_id": CONTRACT_PUBLIC,
        "type": "item_exchange",
        "status": "outstanding",
        "availability": "public",
        "issuer_id": 111,
        "start_location_id": STRUCTURE_IN_SYSTEM,
        "title": "Doctrine Fit",
        "price": 50_000_000.0,
        "date_issued": "2026-01-01T00:00:00Z",
        "date_expired": "2026-01-08T00:00:00Z",
    }
    base.update(overrides)
    return base


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(contract_market, "ESIClient", lambda tokens=None: client)
    monkeypatch.setattr(contract_market, "TokenManager", lambda *a, **k: MagicMock())
    monkeypatch.setattr(contract_market.location_resolver, "resolve_ids", lambda ids, force=False: {})


# ------------------------------------------------------------- pure filters
def test_outstanding_item_exchange_accepts():
    assert contract_market._is_outstanding_item_exchange(_raw()) is True


def test_outstanding_item_exchange_rejects_courier_and_finished():
    assert contract_market._is_outstanding_item_exchange(_raw(type="courier")) is False
    assert contract_market._is_outstanding_item_exchange(_raw(status="finished")) is False


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

    public_raw = [_raw(contract_id=CONTRACT_PUBLIC, availability="public",
                       title="Public Fit", price=40e6)]
    alliance_raw = [_raw(contract_id=CONTRACT_ALLIANCE, availability="alliance",
                         title="Alliance Fit", price=45e6)]
    items = [{"record_id": 1, "type_id": 587, "quantity": 1,
              "is_included": True, "is_singleton": True}]

    client = MagicMock()
    client.public_contracts.return_value = public_raw
    client.public_contract_items.return_value = items
    client.character_contracts.return_value = alliance_raw
    client.character_contract_items.return_value = items
    client.resolve_names.return_value = {111: "Seller One"}
    client.character_public_info.return_value = {"corporation_id": 98000001}
    _patch_client(monkeypatch, client)

    monkeypatch.setattr(
        contract_market, "list_shared_doctrine_characters",
        lambda tm=None: [("doctrine:1", 42, "Scout")],
    )
    monkeypatch.setattr(contract_market, "shared_owner_ids", lambda *a, **k: [])
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

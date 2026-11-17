"""Observed freight rate per hub from finished courier contracts (audit
package M, fix 3). Aggregates only, shared owners only, never applied."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eve_trader import hubs, storage
from eve_trader.realized import db as ledger_db
from eve_trader.realized import freight_rates
from eve_trader.realized import ingest as ledger_ingest

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_LEDGER_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "production_ledger_schema.sql"

ALICE, BOB = 900001, 900002
HOME = 1_040_000_000_001
JITA_STATION = 60003760
JITA_STRUCTURE = 1_040_000_000_777  # a player structure in the Jita system
AMARR_STATION = 60008494
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def _apply_production_ledger_schema(_apply_phase1_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_LEDGER_SCHEMA_SQL.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _setup(_apply_production_ledger_schema, monkeypatch):
    pg_helpers.wipe_tables("ledger_contracts", "ledger_contract_items", "ledger_archive_gaps",
                           "ledger_sync_state", "esi_sharing")
    monkeypatch.setattr(freight_rates, "home_location_id", lambda: HOME)
    monkeypatch.setattr(ledger_db, "region_id_for_location", lambda loc: 10000002)
    systems = {30000142: [30000142, JITA_STATION, JITA_STRUCTURE], 30002187: [30002187, AMARR_STATION]}
    monkeypatch.setattr(storage, "location_ids_in_systems",
                        lambda ids: sorted({x for s in ids for x in systems.get(s, [])}))
    yield


def _share(owner_id, kind="contracts", tool="production"):
    with storage.connect() as conn:
        conn.execute("INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                     "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING", (owner_id, kind, tool))


_next_id = [1000]


def _courier(owner, start, *, status="finished", volume=100.0, reward=70_000.0, days_ago=10,
             end=HOME, issued=None, issuer=None):
    _next_id[0] += 1
    when = issued or (NOW - timedelta(days=days_ago, hours=_next_id[0] % 7 * 24))
    ledger_ingest.archive_contracts(
        owner_type="character", owner_id=owner,
        contracts=[{
            "contract_id": _next_id[0], "type": "courier", "status": status,
            "issuer_id": issuer or owner, "acceptor_id": 1, "start_location_id": start,
            "end_location_id": end, "date_issued": when.isoformat(), "volume": volume,
            "reward": reward, "collateral": 1_000_000_000,
            "date_completed": when.isoformat() if status == "finished" else None,
        }],
        items_by_contract={}, now=NOW,
    )


def _jita(rows):
    return next(r for r in rows if r["region_id"] == 10000002)


def test_rate_is_reward_over_volume_of_the_hubs_shipments_only(tenant):
    _share(ALICE)
    for start in (JITA_STATION, JITA_STATION, JITA_STATION):
        _courier(ALICE, start, volume=100.0, reward=70_000.0)
    _courier(ALICE, JITA_STATION, volume=300.0, reward=300_000.0)  # sum 570k / 600 = 950
    for _ in range(3):  # a second system must not leak into Jita's figure
        _courier(ALICE, AMARR_STATION, volume=100.0, reward=200_000.0)
    rows = freight_rates.observed_freight_all(NOW)
    assert [r["region_id"] for r in rows] == list(hubs.TRADE_HUBS)
    jita = _jita(rows)
    assert jita["reason"] is None
    # Couriers issued minutes apart would merge; these are days apart, so 4 shipments.
    assert jita["shipments"] == 4
    assert jita["suggested"] == round((3 * 70_000 + 300_000) / 600)
    assert jita["volume_m3"] == 600.0
    amarr = next(r for r in rows if r["region_id"] == 10000043)
    assert amarr["suggested"] == 2000
    assert next(r for r in rows if r["region_id"] == 10000032)["reason"] == "too_few_shipments"


def test_a_structure_in_the_hub_system_counts(tenant):
    _share(ALICE)
    _courier(ALICE, JITA_STATION, volume=100.0, reward=80_000.0)
    _courier(ALICE, JITA_STRUCTURE, volume=100.0, reward=60_000.0)
    _courier(ALICE, JITA_STRUCTURE, volume=100.0, reward=60_000.0)
    jita = _jita(freight_rates.observed_freight_all(NOW))
    assert jita["shipments"] == 3
    assert jita["suggested"] == round(200_000 / 300)


def test_failed_expired_unfinished_and_foreign_destination_do_not_count(tenant):
    _share(ALICE)
    for _ in range(2):
        _courier(ALICE, JITA_STATION)
    _courier(ALICE, JITA_STATION, status="failed")
    _courier(ALICE, JITA_STATION, status="expired")
    _courier(ALICE, JITA_STATION, status="deleted")
    _courier(ALICE, JITA_STATION, end=42)  # does not end at the home structure
    jita = _jita(freight_rates.observed_freight_all(NOW))
    assert jita["shipments"] == 2
    assert jita["suggested"] is None
    assert jita["reason"] == "too_few_shipments"
    assert "3" in jita["message"] and "90" in jita["message"]


def test_old_couriers_fall_out_of_the_window(tenant):
    _share(ALICE)
    for _ in range(3):
        _courier(ALICE, JITA_STATION, issued=NOW - timedelta(days=freight_rates.WINDOW_DAYS + 5))
    jita = _jita(freight_rates.observed_freight_all(NOW))
    assert jita["suggested"] is None and jita["reason"] == "no_couriers"


def test_an_owner_that_shares_contracts_with_no_realized_tool_stays_out(tenant):
    _share(ALICE)
    _share(BOB, tool="char_info")  # not a realized-profit tool
    for _ in range(3):
        _courier(ALICE, JITA_STATION, volume=100.0, reward=50_000.0)
    for _ in range(3):
        _courier(BOB, JITA_STATION, volume=100.0, reward=900_000.0)
    jita = _jita(freight_rates.observed_freight_all(NOW))
    assert jita["shipments"] == 3
    assert jita["suggested"] == 500


def test_a_trading_only_contracts_share_still_supplies_couriers(tenant):
    # KIND_TOOLS["contracts"] includes Trading: couriers are not item exchanges.
    _share(ALICE, tool="trading")
    for _ in range(3):
        _courier(ALICE, JITA_STATION, volume=100.0, reward=60_000.0)
    assert _jita(freight_rates.observed_freight_all(NOW))["suggested"] == 600


def test_no_shared_owner_and_no_home_structure_give_a_reason(tenant, monkeypatch):
    rows = freight_rates.observed_freight_all(NOW)
    assert {r["reason"] for r in rows} == {"no_couriers"} and all(r["suggested"] is None for r in rows)
    monkeypatch.setattr(freight_rates, "home_location_id", lambda: None)
    assert {r["reason"] for r in freight_rates.observed_freight_all(NOW)} == {"no_home_structure"}


def test_response_carries_no_contract_ids_or_owners(tenant):
    _share(ALICE)
    for _ in range(3):
        _courier(ALICE, JITA_STATION)
    row = _jita(freight_rates.observed_freight_all(NOW))
    assert set(row) == {"region_id", "hub", "source", "suggested", "shipments", "volume_m3",
                        "window_days", "min_shipments", "start", "end", "reason", "message"}
    assert freight_rates.observed_freight(10000016, NOW) is None

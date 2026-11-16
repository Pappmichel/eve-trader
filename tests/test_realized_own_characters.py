"""Own characters of the realized-profit ledger: registry, tokens, and
characters with sharing rows, minus characters removed on the Characters
page that hold no token (`realized.loader._tenant_character_ids`)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from eve_trader import storage
from eve_trader.auth import TokenRecord
from eve_trader.doctrine.config import DoctrineConfig
from eve_trader.esi_data import actions as esi_actions
from eve_trader.realized import db as ledger_db
from eve_trader.realized import loader

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 930001      # registry (login) character
BOB = 930002        # no token, sharing rows only
OUTSIDER = 930099
CORP = 98000931
TYPE_A = 34
LOC = 60003760
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
CONTRACT = 7700


@pytest.fixture(autouse=True)
def _wipe(_apply_phase1_schema):
    tables = (
        "ledger_wallet_transactions", "ledger_wallet_journal", "ledger_industry_jobs",
        "ledger_job_inputs", "ledger_job_planned_costs", "ledger_valuation_prices", "ledger_bpc_run_prices",
        "ledger_blueprints", "ledger_market_orders", "ledger_contracts", "ledger_contract_items",
        "ledger_archive_gaps", "ledger_sync_state", "ledger_reference_prices",
        "prod_cost_layers", "prod_realized_results", "prod_cost_draws", "prod_job_records",
        "prod_fee_lines", "prod_job_losses", "prod_capital_bpos", "prod_inventory_drift",
        "prod_opening_frozen", "prod_opening_lines", "prod_ledger_runs",
        "esi_sharing", "esi_character_capabilities", "esi_removed_characters", "tenant_tokens",
    )
    pg_helpers.wipe_tables(*tables)
    yield
    pg_helpers.wipe_tables(*tables)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    from eve_trader.realized import names

    monkeypatch.setattr(loader, "_planner_values", lambda type_ids: {})
    monkeypatch.setattr(names, "_esi_names", lambda ids: {})
    monkeypatch.setattr(storage, "list_tenant_registry_entries",
                        lambda tenant_id: [("character", ALICE)])


def _share(owner_type, owner_id, kind, tool="production"):
    storage.upsert_esi_sharing(owner_type, owner_id, kind, tool)


def _save_token(character_id):
    rec = TokenRecord(
        role=f"esi:{character_id}", character_id=character_id, character_name=str(character_id),
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes="",
    )
    storage.save_tenant_token(rec.role, asdict(rec))


def _seed_contract_between_alice_and_bob():
    """ALICE buys 10, gives 4 to BOB by contract for nothing. BOB has a
    wallet row of his own, so he has archive rows in every rebuild."""
    for kind in ("wallet", "contracts"):
        _share("character", ALICE, kind)
    _share("character", BOB, "wallet")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 100.0, 10, True, 101, None, None, None),
        ("character", BOB, 0, 2, T0, TYPE_A, LOC, 100.0, 1, True, 102, None, None, None),
    ])
    ledger_db.upsert_ledger_contracts([
        ("character", ALICE, CONTRACT, ALICE, None, BOB, LOC, LOC, "item_exchange", "finished",
         None, 0.0, None, T1, T1, None),
    ])
    ledger_db.upsert_ledger_contract_items([
        ("character", ALICE, CONTRACT, 1, TYPE_A, 4, True, False),
    ])


def _rebuild():
    with patch.object(loader, "_opening_stock", return_value={}), \
            patch.object(loader, "_fetch_reference_prices",
                         side_effect=lambda requests, today: {key: 90.0 for key in requests}), \
            patch.object(loader, "_production_type_ids", return_value=frozenset({TYPE_A})), \
            patch.object(loader, "_relic_type_ids", return_value=frozenset()):
        return loader.rebuild_realized()


def _contract_results():
    with storage.connect() as conn:
        return conn.execute(
            "SELECT sale_ref, revenue, cost FROM prod_realized_results WHERE source = 'contract'"
        ).fetchall()


def _foreign(summary):
    return [w for w in summary["warnings"] if w.startswith("foreign_character_with_archive")]


def test_contract_with_a_sharing_only_character_is_internal_until_it_is_removed(tenant):
    _seed_contract_between_alice_and_bob()

    # BOB has no token, only sharing rows: he is one of the tenant's own.
    summary = _rebuild()
    assert _contract_results() == []
    assert _foreign(summary) == []

    # Removed on the Characters page (needs a token to remove): the sharing
    # rows stay, but BOB is an outsider now and the hand-over is a sale.
    _save_token(BOB)
    esi_actions.do_remove_token_character(BOB)
    summary = _rebuild()
    rows = _contract_results()
    assert [r[0] for r in rows] == [f"contract:{CONTRACT}"]
    assert float(rows[0][1]) == 0.0 and float(rows[0][2]) == pytest.approx(400.0)
    removed_on = storage.list_esi_removed_characters()[BOB]
    assert _foreign(summary) == [
        f"foreign_character_with_archive: character {BOB} has archive rows but is not an own "
        f"character (removed {removed_on.date().isoformat()}); contracts with it count as sales"
    ]
    # The warning reaches the stored run like every other rebuild warning.
    assert any(w.startswith("foreign_character_with_archive") for w in ledger_db.latest_ledger_run()[3])

    # Added again (a token): own again, without deleting the removal row.
    _save_token(BOB)
    summary = _rebuild()
    assert _contract_results() == []
    assert _foreign(summary) == []
    assert BOB in storage.list_esi_removed_characters()


def test_corp_job_installed_by_a_sharing_only_character_is_counted(tenant):
    _share("character", ALICE, "wallet")
    _share("character", BOB, "assets", "doctrine")       # any sharing row makes him own
    _share("corporation", CORP, "industry_jobs")
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, TYPE_A, LOC, 100.0, 10, True, 101, None, None, None),
    ])
    ledger_db.upsert_ledger_industry_jobs([
        ("corporation", CORP, 55, 1, None, 1000, TYPE_A, 1, None, None,
         10.0, LOC, LOC, LOC, BOB, T1, T1, None, "active", None),
    ])
    summary = _rebuild()
    assert not any(w.startswith("skipped_corp_job") for w in summary["warnings"])
    with storage.connect() as conn:
        jobs = conn.execute("SELECT job_id FROM prod_job_records").fetchall()
    assert [int(r[0]) for r in jobs] == [55]


def test_doctrine_contract_of_a_sharing_only_character_stays_doctrine(tenant, monkeypatch):
    _share("character", BOB, "contracts", "doctrine")
    ledger_db.upsert_ledger_contracts([
        ("character", BOB, CONTRACT, BOB, None, OUTSIDER, LOC, LOC, "item_exchange", "finished",
         "Fit", 1_000_000.0, None, T1, T1, None),
    ])
    ledger_db.upsert_ledger_contract_items([
        ("character", BOB, CONTRACT, 1, TYPE_A, 1, True, False),
    ])
    monkeypatch.setattr(loader, "DOCTRINE_CONFIG", DoctrineConfig(doctrine_structure_ids=[LOC]))
    monkeypatch.setattr(loader, "_doctrine_fitting_ids", lambda ids: {CONTRACT: "fit-1"})
    monkeypatch.setattr(ledger_db, "region_id_for_location", lambda loc: None)

    contracts = loader._load_contracts(loader._tenant_character_ids())

    assert [(c.contract_id, c.is_doctrine, c.fitting_id) for c in contracts] == [
        (CONTRACT, True, "fit-1"),
    ]

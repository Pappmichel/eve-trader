"""Overlapping replace_derived_costing calls must not double derived rows."""
from __future__ import annotations

import threading

import pytest

from eve_trader import storage
from eve_trader.realized import db as ledger_db

from . import pg_helpers
from .pg_helpers import tenant  # noqa: F401

pytestmark = pg_helpers.postgres_required()

_TABLES = (
    "prod_cost_layers", "prod_realized_results", "prod_cost_draws",
    "prod_job_records", "prod_fee_lines", "prod_job_losses",
    "prod_capital_bpos", "prod_inventory_drift", "prod_opening_lines",
    "prod_reprocess_events", "prod_ledger_runs", "prod_opening_frozen",
)


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(*_TABLES)
    yield
    pg_helpers.wipe_tables(*_TABLES)


def test_two_overlapping_rebuilds_leave_one_capital_set(tenant):
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _run(amount: float):
        try:
            with storage.tenant_context(tenant):
                barrier.wait(timeout=5)
                ledger_db.replace_derived_costing(
                    layers=[],
                    results=[],
                    cutover_t0=None,
                    warnings=[],
                    capital=[(34, amount)],
                    owner_scope_hash="test",
                    costing_version=5,
                )
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=_run, args=(100.0,))
    t2 = threading.Thread(target=_run, args=(200.0,))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert errors == []
    with storage.connect() as conn:
        rows = conn.execute("SELECT amount FROM prod_capital_bpos").fetchall()
        runs = conn.execute("SELECT count(*) FROM prod_ledger_runs").fetchone()[0]
    assert len(rows) == 1
    assert rows[0][0] in (100.0, 200.0)
    assert runs == 2

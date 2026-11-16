"""Capital hides ledger layers built from other shared owners than today's,
like the realized reports (sharing fingerprint of the last rebuild)."""
from __future__ import annotations

import pytest

from eve_trader import portfolio, storage

from . import pg_helpers
from . import test_realized_bpc_run_prices as ledger_case
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)
from .test_realized_bpc_run_prices import _offline, _wipe  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _offline_prices(monkeypatch):
    monkeypatch.setattr(portfolio, "_priced", lambda ids: {tid: 12.0 for tid in ids})
    monkeypatch.setattr(portfolio, "_bpc_run_values", lambda ids: {})


def test_capital_needs_a_rebuild_without_a_run(tenant):
    result = portfolio.do_get_portfolio_capital()
    assert result["needs_rebuild"] is True
    assert result["hint"]
    assert result["buckets"] == []
    assert result["total"]["cost_basis"] is None and result["total"]["market_value"] is None


def test_capital_hides_layers_after_a_share_is_removed_until_the_next_rebuild(tenant):
    ledger_case._seed()
    ledger_case._rebuild({ledger_case.BLUEPRINT: 20.0})
    before = portfolio.do_get_portfolio_capital()
    assert before["needs_rebuild"] is False and before["hint"] is None
    assert before["total"]["cost_basis"] == pytest.approx(5 * 10.0 + 5 * 30.0)

    storage.delete_esi_sharing("character", ledger_case.ALICE, "industry_jobs", "production")
    hidden = portfolio.do_get_portfolio_capital()
    assert hidden["needs_rebuild"] is True
    assert "Sharing changed" in hidden["hint"]
    assert hidden["buckets"] == [] and hidden["total"]["cost_basis"] is None

    ledger_case._rebuild({ledger_case.BLUEPRINT: 20.0})
    after = portfolio.do_get_portfolio_capital()
    assert after["needs_rebuild"] is False
    assert after["total"]["cost_basis"] is not None

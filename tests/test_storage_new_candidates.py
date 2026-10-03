"""storage.read_latest_new_candidates against real Postgres: only the newest
run is returned, however many older runs the table still holds."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.models import NewCandidateResult

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def _result(type_id: int, add: bool) -> NewCandidateResult:
    return NewCandidateResult(
        item=f"Item {type_id}", category="Module", type_id=type_id, volume_m3=5.0,
        paired_days=10, profitable_days=8, hit_rate=0.8, latest_margin=0.2,
        best_margin=0.3, avg_profit_m3=100.0, avg_sell_movement=4.0, score=1.0,
        recommendation="Consider import", add=add,
    )


def test_read_latest_new_candidates_returns_only_the_newest_run(tenant):
    storage.save_new_candidates([_result(1, True), _result(2, False)], "2026-10-01T10:00:00")
    storage.save_new_candidates([_result(3, True)], "2026-10-02T10:00:00")

    df = storage.read_latest_new_candidates()

    assert df["type_id"].tolist() == [3]
    assert set(df["run_ts"]) == {"2026-10-02T10:00:00"}
    assert int(df["add_flag"].sum()) == 1


def test_read_latest_new_candidates_is_empty_without_any_run(tenant):
    df = storage.read_latest_new_candidates()

    assert df.empty
    assert "add_flag" in df.columns

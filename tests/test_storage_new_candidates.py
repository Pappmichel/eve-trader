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

def test_prune_new_candidates_keeps_only_the_newest_runs(tenant):
    for day in range(1, 5):
        storage.save_new_candidates([_result(day, True), _result(day + 10, False)], f"2026-10-0{day}T10:00:00")

    deleted = storage.prune_new_candidates(keep_runs=2)

    assert deleted == 4
    df = storage.read_table("new_candidates")
    assert sorted(set(df["run_ts"])) == ["2026-10-03T10:00:00", "2026-10-04T10:00:00"]


def test_prune_new_candidates_leaves_other_tenants_alone(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    for t in (tenant_a, tenant_b):
        with storage.tenant_context(t):
            for day in range(1, 4):
                storage.save_new_candidates([_result(day, True)], f"2026-10-0{day}T10:00:00")

    with storage.tenant_context(tenant_a):
        storage.prune_new_candidates(keep_runs=1)

    with storage.tenant_context(tenant_b):
        assert len(storage.read_table("new_candidates")) == 3

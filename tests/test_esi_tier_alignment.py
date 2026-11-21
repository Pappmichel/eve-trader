"""Owners of one tenant are fetched together: when a freshness tier comes due
for one owner, pairs of that tier that are at least half an interval old run
in the same pass (orchestrator._aligned_with)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import storage
from eve_trader.esi_data import orchestrator

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")
pytestmark = pg_helpers.postgres_required()

ALICE, BOB, CAROL, CORP = 11, 22, 33, 98000001
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("esi_sharing", "esi_freshness")
    yield


def _share(owner_type, owner_id, kind, tool="production"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            (owner_type, owner_id, kind, tool),
        )


def _synced(owner_type, owner_id, kind, hours_ago, **kw):
    storage.upsert_esi_freshness(
        owner_type, owner_id, kind, success=True,
        now=(NOW - timedelta(hours=hours_ago)).isoformat(), **kw,
    )


def _due():
    return sorted((r[0], r[1], r[2]) for r in orchestrator.pending_due(now=NOW))


def test_a_due_owner_pulls_others_of_the_tier_that_are_half_an_interval_old(tenant):
    # Normal tier = 6 h. Alice is due, Corp is 4 h old (pulled), Bob 1 h (not).
    for owner_type, owner_id, age in (("character", ALICE, 7), ("corporation", CORP, 4),
                                      ("character", BOB, 1)):
        _share(owner_type, owner_id, "assets")
        _synced(owner_type, owner_id, "assets", age)

    assert _due() == [("character", ALICE, "assets"), ("corporation", CORP, "assets")]


def test_alignment_spans_kinds_of_the_same_tier_only(tenant):
    _share("character", ALICE, "assets")          # normal
    _synced("character", ALICE, "assets", 7)
    _share("character", BOB, "industry_jobs")     # normal
    _synced("character", BOB, "industry_jobs", 3)
    _share("character", CAROL, "blueprints")      # rare (24 h)
    _synced("character", CAROL, "blueprints", 20)

    assert _due() == [("character", ALICE, "assets"), ("character", BOB, "industry_jobs")]


def test_nothing_is_pulled_when_nothing_is_due(tenant):
    for owner_id in (ALICE, BOB):
        _share("character", owner_id, "assets")
        _synced("character", owner_id, "assets", 5)

    assert _due() == []


def test_pulled_pairs_still_respect_expires_and_failure_backoff(tenant):
    _share("character", ALICE, "assets")
    _synced("character", ALICE, "assets", 7)
    _share("character", BOB, "assets")
    _synced("character", BOB, "assets", 4, next_fetch_after=NOW + timedelta(minutes=10))
    _share("character", CAROL, "assets")
    _synced("character", CAROL, "assets", 5)
    storage.upsert_esi_freshness("character", CAROL, "assets", success=False, error="HTTP 500",
                                 now=(NOW - timedelta(hours=1)).isoformat())

    assert _due() == [("character", ALICE, "assets")]


def test_a_refresh_request_alone_does_not_pull_others(tenant):
    _share("character", ALICE, "assets")
    _synced("character", ALICE, "assets", 1)
    _share("character", BOB, "assets")
    _synced("character", BOB, "assets", 4)
    with storage.connect() as conn:
        conn.execute("UPDATE esi_freshness SET refresh_requested_at = ? WHERE owner_id = ?",
                     (NOW - timedelta(minutes=5), ALICE))

    assert _due() == [("character", ALICE, "assets")]


def test_after_one_aligned_pass_the_tier_comes_due_together(tenant):
    for owner_id in (ALICE, BOB):
        _share("character", owner_id, "assets")
    _synced("character", ALICE, "assets", 7)
    _synced("character", BOB, "assets", 4)
    assert len(_due()) == 2

    for owner_id in (ALICE, BOB):  # both fetched in that pass
        _synced("character", owner_id, "assets", 0)
    later = NOW + timedelta(hours=6, minutes=5)
    due = sorted(r[1] for r in orchestrator.pending_due(now=later))
    assert due == [ALICE, BOB]

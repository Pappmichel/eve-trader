"""Phase 3a orchestrator: fake ESIClient, two owners, two kinds.

Covers success, mid-kind failure (decision 6 + batch rollback), overlapping
manual+scheduled guard, production-only sharing, and the NULL-id orphan
sweep (clean pass vs any-failure no-op).
"""
from __future__ import annotations

import threading

import pytest

from eve_trader import storage
from eve_trader.esi_client import ESIError
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.orchestrator import do_sync_for_tool

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
BOB = 1002
TYPE_ID = 34
LOCATION_ID = 1000000000001


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(
        "character_assets", "corp_assets",
        "character_industry_jobs", "corp_industry_jobs",
        "character_blueprints", "corp_blueprints",
        "character_sell_orders", "character_slots",
        "esi_sharing", "esi_freshness",
        "esi_wallet_transactions", "esi_wallet_journal",
    )
    yield
    orchestrator._in_flight.clear()


def _share(owner_type: str, owner_id: int, data_kind: str, tool_key: str) -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            (owner_type, owner_id, data_kind, tool_key),
        )


def _tokens(*chars: tuple[int, str]):
    """`(character_id, name)` -> orchestrator token tuples."""
    return [
        (f"producer:{cid}", cid, name, "producer")
        for cid, name in chars
    ]


def _asset(item_id, owner_id):
    return {
        "item_id": item_id, "type_id": TYPE_ID, "location_id": LOCATION_ID,
        "location_flag": "Hangar", "quantity": owner_id, "is_blueprint_copy": False,
    }


def _job(job_id, owner_id):
    return {
        "job_id": job_id, "activity_id": 1, "blueprint_type_id": 1, "product_type_id": 2,
        "runs": 1, "output_location_id": LOCATION_ID, "status": "active",
        "end_date": "2026-01-01T00:00:00Z", "start_date": "2026-01-01T00:00:00Z",
        "installer_id": owner_id,
    }


class FakeClient:
    def __init__(self, assets=None, jobs=None, job_error_for=None):
        self.assets = assets or {}
        self.jobs = jobs or {}
        self.job_error_for = set(job_error_for or ())
        self.asset_calls: list[int] = []
        self.job_calls: list[int] = []
        self._asset_hook = None

    def character_assets(self, character_id, auth_role):
        if self._asset_hook:
            self._asset_hook(character_id)
        self.asset_calls.append(character_id)
        return list(self.assets.get(character_id, []))

    def character_industry_jobs(self, character_id, auth_role):
        self.job_calls.append(character_id)
        if character_id in self.job_error_for:
            raise ESIError("jobs 403")
        return list(self.jobs.get(character_id, []))

    def character_public_info(self, character_id):
        return {}

    def corporation_public_info(self, corporation_id):
        return {"name": str(corporation_id)}

    def resolve_names(self, ids):
        return {i: str(i) for i in ids}


def _count(table, **where) -> int:
    clause = " AND ".join(f"{k} = ?" for k in where)
    sql = f"SELECT count(*) FROM {table}"
    params = tuple(where.values())
    if clause:
        sql += f" WHERE {clause}"
    with storage.connect() as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_orchestrator_two_owners_two_kinds_success(tenant, monkeypatch):
    _share("character", ALICE, "assets", "production")
    _share("character", ALICE, "industry_jobs", "production")
    _share("character", BOB, "assets", "production")
    _share("character", BOB, "industry_jobs", "production")
    monkeypatch.setattr(orchestrator, "_list_token_characters", lambda tm: _tokens(
        (ALICE, "Alice"), (BOB, "Bob"),
    ))
    client = FakeClient(
        assets={ALICE: [_asset(1, ALICE)], BOB: [_asset(2, BOB)]},
        jobs={ALICE: [_job(11, ALICE)], BOB: [_job(12, BOB)]},
    )
    result = do_sync_for_tool("production", client=client)
    assert result["ok"] is True
    assert _count("character_assets", owner_character_id=ALICE) == 1
    assert _count("character_assets", owner_character_id=BOB) == 1
    assert _count("character_industry_jobs", owner_character_id=ALICE) == 1
    assert _count("character_industry_jobs", owner_character_id=BOB) == 1
    assert sorted(client.asset_calls) == [ALICE, BOB]
    assert sorted(client.job_calls) == [ALICE, BOB]


def test_orchestrator_mid_kind_failure_rolls_back_that_owner(tenant, monkeypatch):
    # Seed yesterday's assets for Alice so a rolled-back this-run write
    # leaves them (decision 6), rather than committing a half-written
    # owner-run (decision 7).
    storage.replace_assets(
        "character_assets",
        [(99, TYPE_ID, LOCATION_ID, "Hangar", 5, 0, "Alice")],
        owner_character_id=ALICE, owner_name="Alice",
    )
    _share("character", ALICE, "assets", "production")
    _share("character", ALICE, "industry_jobs", "production")
    _share("character", BOB, "assets", "production")
    _share("character", BOB, "industry_jobs", "production")
    monkeypatch.setattr(orchestrator, "_list_token_characters", lambda tm: _tokens(
        (ALICE, "Alice"), (BOB, "Bob"),
    ))
    client = FakeClient(
        assets={ALICE: [_asset(1, ALICE)], BOB: [_asset(2, BOB)]},
        jobs={BOB: [_job(12, BOB)]},
        job_error_for={ALICE},
    )
    result = do_sync_for_tool("production", client=client)
    assert result["ok"] is False
    # Alice's this-run asset write rolled back with the failed jobs kind.
    assert _count("character_assets", owner_character_id=ALICE) == 1
    with storage.connect() as conn:
        qty = conn.execute(
            "SELECT quantity FROM character_assets WHERE owner_character_id = ?",
            (ALICE,),
        ).fetchone()[0]
    assert qty == 5
    assert _count("character_industry_jobs", owner_character_id=ALICE) == 0
    # Bob is a separate owner task and still commits.
    assert _count("character_assets", owner_character_id=BOB) == 1
    assert _count("character_industry_jobs", owner_character_id=BOB) == 1
    with storage.connect() as conn:
        err = conn.execute(
            "SELECT last_error FROM esi_freshness "
            "WHERE owner_type = 'character' AND owner_id = ? AND data_kind = 'industry_jobs'",
            (ALICE,),
        ).fetchone()
    assert err is not None and err[0]


def test_orchestrator_overlapping_call_skips_in_flight_owner(tenant, monkeypatch):
    _share("character", ALICE, "assets", "production")
    monkeypatch.setattr(orchestrator, "_list_token_characters", lambda tm: _tokens((ALICE, "Alice")))
    started = threading.Event()
    release = threading.Event()
    client = FakeClient(assets={ALICE: [_asset(1, ALICE)]})
    tid = storage.get_current_tenant()

    def hook(character_id):
        started.set()
        release.wait(timeout=5)

    client._asset_hook = hook
    skipped = []

    def other():
        with storage.tenant_context(tid):
            skipped.append(do_sync_for_tool("production", client=FakeClient(assets={ALICE: []})))

    def first_run():
        with storage.tenant_context(tid):
            do_sync_for_tool("production", client=client)

    t = threading.Thread(target=other)
    first = threading.Thread(target=first_run)
    first.start()
    assert started.wait(timeout=5)
    t.start()
    t.join(timeout=5)
    release.set()
    first.join(timeout=5)
    assert skipped and skipped[0]["owners"][0].get("skipped") == "in_flight"


def test_orchestrator_production_tool_refreshes_only_production_sharing(tenant, monkeypatch):
    _share("character", ALICE, "assets", "production")
    _share("character", BOB, "assets", "doctrine")
    monkeypatch.setattr(orchestrator, "_list_token_characters", lambda tm: _tokens(
        (ALICE, "Alice"), (BOB, "Bob"),
    ))
    client = FakeClient(assets={ALICE: [_asset(1, ALICE)], BOB: [_asset(2, BOB)]})
    do_sync_for_tool("production", client=client)
    assert client.asset_calls == [ALICE]
    assert _count("character_assets", owner_character_id=ALICE) == 1
    assert _count("character_assets", owner_character_id=BOB) == 0


def test_null_id_sweep_runs_after_clean_pass(tenant, monkeypatch):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_blueprints "
            "(item_id, type_id, location_id, location_flag, quantity, "
            "material_efficiency, time_efficiency, runs) "
            "VALUES (1, 34, 1, 'Hangar', -1, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO corp_blueprints "
            "(item_id, type_id, location_id, location_flag, quantity, "
            "material_efficiency, time_efficiency, runs) "
            "VALUES (2, 34, 1, 'Hangar', -1, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO corp_industry_jobs "
            "(job_id, activity_id, blueprint_type_id, runs, status, end_date, installer_name) "
            "VALUES (3, 1, 34, 1, 'active', '2026-01-01T00:00:00Z', 'ghost')"
        )
    _share("character", ALICE, "assets", "production")
    monkeypatch.setattr(orchestrator, "_list_token_characters", lambda tm: _tokens((ALICE, "Alice")))
    result = do_sync_for_tool("production", client=FakeClient(assets={ALICE: []}))
    assert result["ok"] is True
    assert result["null_id_sweep"] is not None
    assert _count("character_blueprints") == 0
    assert _count("corp_blueprints") == 0
    assert _count("corp_industry_jobs") == 0


def test_null_id_sweep_skipped_after_a_failure(tenant, monkeypatch):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_blueprints "
            "(item_id, type_id, location_id, location_flag, quantity, "
            "material_efficiency, time_efficiency, runs) "
            "VALUES (1, 34, 1, 'Hangar', -1, 0, 0, 0)"
        )
    _share("character", ALICE, "assets", "production")
    _share("character", ALICE, "industry_jobs", "production")
    monkeypatch.setattr(orchestrator, "_list_token_characters", lambda tm: _tokens((ALICE, "Alice")))
    client = FakeClient(assets={ALICE: [_asset(1, ALICE)]}, job_error_for={ALICE})
    result = do_sync_for_tool("production", client=client)
    assert result["ok"] is False
    assert result["null_id_sweep"] is None
    assert _count("character_blueprints") == 1

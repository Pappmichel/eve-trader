"""Phase 3a orchestrator: fake ESIClient, two owners, two kinds.

Covers success, mid-kind failure (decision 6 + batch rollback), overlapping
manual+scheduled guard, production-only sharing, and the NULL-id orphan
sweep (clean pass vs any-failure no-op).
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict

import pytest
import requests

from eve_trader import storage
from eve_trader.auth import TokenManager, TokenRecord
from eve_trader.esi_client import ESIClient, ESIError
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.orchestrator import (
    _run_corporation_kinds_for_members, do_sync_due, do_sync_for_tool,
)
from eve_trader.esi_data.selector import REAUTH_NEEDED

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
ASSETS_SCOPE = "esi-assets.read_assets.v1"
JOBS_SCOPE = "esi-industry.read_character_jobs.v1"
PRODUCER_SCOPES = f"{ASSETS_SCOPE} {JOBS_SCOPE}"
CORP_ID = 98000001
CORP_ASSETS_SCOPE = "esi-assets.read_corporation_assets.v1"


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(
        "character_assets", "corp_assets",
        "character_industry_jobs", "corp_industry_jobs",
        "character_blueprints", "corp_blueprints",
        "character_sell_orders", "character_slots",
        "esi_sharing", "esi_freshness",
        "esi_wallet_transactions", "esi_wallet_journal",
        "tenant_tokens",
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


def _tokens(*chars: tuple[int, str], scopes: str = PRODUCER_SCOPES):
    """Seed real tenant_tokens rows. The orchestrator asks the selector,
    not a prefix listing.
    """
    for cid, name in chars:
        role = f"producer:{cid}"
        storage.save_tenant_token(role, asdict(TokenRecord(
            role=role, character_id=cid, character_name=name,
            access_token="a", refresh_token="r", expires_at=9999999999.0,
            scopes=scopes,
        )))


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
        self.asset_roles: list[str] = []
        self.job_roles: list[str] = []
        self._asset_hook = None

    def character_assets(self, character_id, auth_role):
        if self._asset_hook:
            self._asset_hook(character_id)
        self.asset_calls.append(character_id)
        self.asset_roles.append(auth_role)
        return list(self.assets.get(character_id, []))

    def character_industry_jobs(self, character_id, auth_role):
        self.job_calls.append(character_id)
        self.job_roles.append(auth_role)
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
    _tokens((ALICE, "Alice"), (BOB, "Bob"))
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
    _tokens((ALICE, "Alice"), (BOB, "Bob"))
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
    _tokens((ALICE, "Alice"))
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


def test_null_id_sweep_skipped_when_any_owner_in_flight(tenant, monkeypatch):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_blueprints "
            "(item_id, type_id, location_id, location_flag, quantity, "
            "material_efficiency, time_efficiency, runs) "
            "VALUES (1, 34, 1, 'Hangar', -1, 0, 0, 0)"
        )
    _share("character", ALICE, "assets", "production")
    _share("character", BOB, "assets", "production")
    _tokens((ALICE, "Alice"), (BOB, "Bob"))
    started = threading.Event()
    release = threading.Event()
    client = FakeClient(assets={ALICE: [_asset(1, ALICE)], BOB: [_asset(2, BOB)]})
    tid = storage.get_current_tenant()

    def hook(character_id):
        if character_id == ALICE:
            started.set()
            release.wait(timeout=5)

    client._asset_hook = hook
    second = []

    def other():
        with storage.tenant_context(tid):
            second.append(do_sync_for_tool(
                "production",
                client=FakeClient(assets={ALICE: [], BOB: [_asset(2, BOB)]}),
            ))

    def first_run():
        with storage.tenant_context(tid):
            do_sync_for_tool("production", client=client)

    t = threading.Thread(target=other)
    first = threading.Thread(target=first_run)
    first.start()
    assert started.wait(timeout=5)
    t.start()
    t.join(timeout=5)
    assert second and second[0]["null_id_sweep"] is None
    assert _count("character_blueprints") == 1
    release.set()
    first.join(timeout=5)


def test_orchestrator_production_tool_refreshes_only_production_sharing(tenant, monkeypatch):
    _share("character", ALICE, "assets", "production")
    _share("character", BOB, "assets", "doctrine")
    _tokens((ALICE, "Alice"), (BOB, "Bob"))
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
    _tokens((ALICE, "Alice"))
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
    _tokens((ALICE, "Alice"))
    client = FakeClient(assets={ALICE: [_asset(1, ALICE)]}, job_error_for={ALICE})
    result = do_sync_for_tool("production", client=client)
    assert result["ok"] is False
    assert result["null_id_sweep"] is None
    assert _count("character_blueprints") == 1


def test_orchestrator_records_reauth_needed_and_keeps_processing_other_owners(tenant):
    """No token holding a kind's scope is not a fetch failure: that owner ×
    kind is marked re-auth-needed and other owners still run.
    """
    _share("character", ALICE, "assets", "production")
    _share("character", ALICE, "industry_jobs", "production")
    _share("character", BOB, "assets", "production")
    _share("character", BOB, "industry_jobs", "production")
    _tokens((ALICE, "Alice"), scopes=ASSETS_SCOPE)
    _tokens((BOB, "Bob"), scopes=PRODUCER_SCOPES)
    client = FakeClient(
        assets={ALICE: [_asset(1, ALICE)], BOB: [_asset(2, BOB)]},
        jobs={ALICE: [_job(11, ALICE)], BOB: [_job(12, BOB)]},
    )
    result = do_sync_for_tool("production", client=client)
    assert result["ok"] is True
    alice = next(r for r in result["owners"] if r["owner_id"] == ALICE)
    assert alice["ok"] is True
    assert alice["kinds"]["assets"]["written"] == 1
    assert alice["kinds"]["industry_jobs"] == REAUTH_NEEDED
    assert _count("character_assets", owner_character_id=ALICE) == 1
    assert _count("character_industry_jobs", owner_character_id=ALICE) == 0
    assert _count("character_assets", owner_character_id=BOB) == 1
    assert _count("character_industry_jobs", owner_character_id=BOB) == 1
    assert ALICE not in client.job_calls
    with storage.connect() as conn:
        err = conn.execute(
            "SELECT last_error FROM esi_freshness "
            "WHERE owner_type = 'character' AND owner_id = ? AND data_kind = 'industry_jobs'",
            (ALICE,),
        ).fetchone()
    assert err is None


def test_do_sync_due_fetches_only_kinds_past_their_interval(tenant):
    _share("character", ALICE, "assets", "production")
    _share("character", ALICE, "industry_jobs", "production")
    _tokens((ALICE, "Alice"))
    storage.upsert_esi_freshness("character", ALICE, "assets", success=True)
    client = FakeClient(
        assets={ALICE: [_asset(1, ALICE)]},
        jobs={ALICE: [_job(11, ALICE)]},
    )
    result = do_sync_due(client=client)
    assert result["ok"] is True
    assert client.asset_calls == []
    assert client.job_calls == [ALICE]
    assert _count("character_assets", owner_character_id=ALICE) == 0
    assert _count("character_industry_jobs", owner_character_id=ALICE) == 1


def test_manual_tool_sync_pushes_back_the_next_scheduled_fetch(tenant):
    _share("character", ALICE, "assets", "production")
    _share("character", ALICE, "industry_jobs", "production")
    _tokens((ALICE, "Alice"))
    first = FakeClient(
        assets={ALICE: [_asset(1, ALICE)]},
        jobs={ALICE: [_job(11, ALICE)]},
    )
    assert do_sync_for_tool("production", client=first)["ok"] is True
    assert sorted(first.asset_calls) == [ALICE]
    assert sorted(first.job_calls) == [ALICE]
    second = FakeClient(
        assets={ALICE: [_asset(2, ALICE)]},
        jobs={ALICE: [_job(12, ALICE)]},
    )
    result = do_sync_due(client=second)
    assert result["ok"] is True
    assert second.asset_calls == []
    assert second.job_calls == []
    assert _count("character_assets", owner_character_id=ALICE) == 1
    assert _count("character_industry_jobs", owner_character_id=ALICE) == 1


def test_orchestrator_picks_largest_scope_token_not_a_prefix(tenant):
    """Two keys for one character: the orchestrator asks the selector,
    so the larger scope set wins rather than a `producer:` listing.
    """
    _share("character", ALICE, "assets", "production")
    _share("character", ALICE, "industry_jobs", "production")
    storage.save_tenant_token("doctrine-assets:1001", asdict(TokenRecord(
        role="doctrine-assets:1001", character_id=ALICE, character_name="Alice",
        access_token="a", refresh_token="r", expires_at=9999999999.0,
        scopes=ASSETS_SCOPE,
    )))
    storage.save_tenant_token("producer:1001", asdict(TokenRecord(
        role="producer:1001", character_id=ALICE, character_name="Alice",
        access_token="a", refresh_token="r", expires_at=9999999999.0,
        scopes=PRODUCER_SCOPES,
    )))
    client = FakeClient(
        assets={ALICE: [_asset(1, ALICE)]},
        jobs={ALICE: [_job(11, ALICE)]},
    )
    result = do_sync_for_tool("production", client=client)
    assert result["ok"] is True
    assert client.asset_roles == ["producer:1001"]
    assert client.job_roles == ["producer:1001"]
    assert _count("character_assets", owner_character_id=ALICE) == 1
    assert _count("character_industry_jobs", owner_character_id=ALICE) == 1


class _EsiResp:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


def _invalid_grant_response(url: str) -> requests.Response:
    resp = requests.Response()
    resp.status_code = 400
    resp.reason = "Bad Request"
    resp.url = url
    resp._content = b'{"error":"invalid_grant"}'
    return resp


def _patch_dead_refresh(monkeypatch, *, esi_handler):
    """SSO refresh returns invalid_grant; ESI GETs go through `esi_handler`."""
    refresh_tokens = []

    def fake_post(url, data=None, headers=None, timeout=30, **kwargs):
        refresh_tokens.append((data or {}).get("refresh_token"))
        return _invalid_grant_response(url)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests.Session, "get", esi_handler)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    return refresh_tokens


def test_corp_member_loop_continues_when_first_member_refresh_fails(tenant, monkeypatch):
    """Highest-value consequence of the HTTPError leak: a dead token on the
    first corp member used to abort the whole corporation before a later
    member with a valid Director token was tried.
    """
    storage.save_tenant_token("producer:1001", asdict(TokenRecord(
        role="producer:1001", character_id=ALICE, character_name="Alice",
        access_token="stale", refresh_token="revoked", expires_at=0.0,
        scopes=CORP_ASSETS_SCOPE,
    )))
    storage.save_tenant_token("producer:1002", asdict(TokenRecord(
        role="producer:1002", character_id=BOB, character_name="Bob",
        access_token="good", refresh_token="live", expires_at=9999999999.0,
        scopes=CORP_ASSETS_SCOPE,
    )))
    asset_auths = []

    def fake_get(self, url, params=None, headers=None, timeout=30):
        if "/assets/" in url:
            asset_auths.append((headers or {}).get("Authorization"))
            return _EsiResp(200, [_asset(1, CORP_ID)], headers={"X-Pages": "1"})
        raise AssertionError(f"unexpected ESI GET {url}")

    refresh_tokens = _patch_dead_refresh(monkeypatch, esi_handler=fake_get)
    tm = TokenManager()
    alice = tm.get_record("producer:1001")
    bob = tm.get_record("producer:1002")
    assert alice is not None and bob is not None
    result = _run_corporation_kinds_for_members(
        client=ESIClient(tokens=tm), corp_id=CORP_ID, kinds=["assets"],
        members=[alice, bob], corp_name="Test Corp", extra={}, tokens=tm,
    )
    assert result["ok"] is True
    assert refresh_tokens == ["revoked"]
    assert asset_auths == ["Bearer good"]
    assert _count("corp_assets", owner_corporation_id=CORP_ID) == 1
    with storage.connect() as conn:
        err = conn.execute(
            "SELECT last_error FROM esi_freshness "
            "WHERE owner_type = 'corporation' AND owner_id = ? AND data_kind = 'assets'",
            (CORP_ID,),
        ).fetchone()
    assert err is not None and err[0] is None


def test_dead_refresh_token_stamps_freshness_last_error_for_kind(tenant, monkeypatch):
    """Without the ESIError conversion the outer handler recorded kind '?'
    and _record_failure refused to write, so Characters showed nothing.
    """
    _share("character", ALICE, "assets", "production")
    storage.save_tenant_token("producer:1001", asdict(TokenRecord(
        role="producer:1001", character_id=ALICE, character_name="Alice",
        access_token="stale", refresh_token="revoked", expires_at=0.0,
        scopes=ASSETS_SCOPE,
    )))
    esi_asset_gets = []

    def fake_get(self, url, params=None, headers=None, timeout=30):
        if "/assets/" in url:
            esi_asset_gets.append(url)
            return _EsiResp(200, [_asset(1, ALICE)], headers={"X-Pages": "1"})
        if "/characters/" in url:
            return _EsiResp(200, {"corporation_id": CORP_ID})
        if "/corporations/" in url:
            return _EsiResp(200, {"name": "Test Corp"})
        raise AssertionError(f"unexpected ESI GET {url}")

    _patch_dead_refresh(monkeypatch, esi_handler=fake_get)
    result = do_sync_for_tool("production", client=ESIClient())
    assert result["ok"] is False
    assert esi_asset_gets == []
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT data_kind, last_error FROM esi_freshness "
            "WHERE owner_type = 'character' AND owner_id = ?",
            (ALICE,),
        ).fetchall()
    assert len(rows) == 1
    kind, err = rows[0]
    assert kind == "assets"
    assert err
    assert "Token refresh failed for role 'producer:1001'" in err
    assert "Re-authorize this character" in err
    assert "?" not in {r[0] for r in rows}

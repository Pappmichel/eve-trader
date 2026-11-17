"""ESI `Expires` as a lower bound for "due" (audit package M, fix 8).

ESI serves an owner endpoint from its cache until `Expires`; a fetch before
then returns the same data. The latest `Expires` of a fetch is stored as
`esi_freshness.next_fetch_after`. The scheduler never treats a pair as due
before it, a manual sync skips it and says "unchanged until ...". No usable
header changes nothing. The tier interval stays a lower bound too.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from eve_trader import esi_client, storage
from eve_trader.auth import TokenRecord
from eve_trader.esi_client import ESIClient, collect_expires, collect_last_modified
from eve_trader.esi_data import orchestrator
from eve_trader.esi_data.orchestrator import do_sync_all, do_sync_due, do_sync_for_tool

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
TYPE_ID = 34
LOCATION_ID = 1000000000001
SCOPES = "esi-assets.read_assets.v1 esi-industry.read_character_jobs.v1"


def _http_date(moment: datetime) -> str:
    return format_datetime(moment.astimezone(timezone.utc), usegmt=True)


@pytest.fixture(autouse=True)
def _wipe(monkeypatch):
    pg_helpers.wipe_tables(
        "character_assets", "character_industry_jobs", "esi_sharing", "esi_freshness",
        "tenant_tokens",
    )
    monkeypatch.setattr(orchestrator, "_resolve_locations", lambda: {"resolved": 0})
    yield
    orchestrator._in_flight.clear()


def _share(kind: str, tool_key: str = "production") -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
            (ALICE, kind, tool_key),
        )


def _token() -> None:
    storage.save_tenant_token(f"producer:{ALICE}", asdict(TokenRecord(
        role=f"producer:{ALICE}", character_id=ALICE, character_name="Alice",
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=SCOPES,
    )))


class _Resp:
    def __init__(self, expires: str | None):
        self.headers = {} if expires is None else {"Expires": expires}


class HeaderClient:
    """Fake ESI client: every call goes through the real header collector
    (`_note_last_modified`) with an `Expires` header `ttl` away from now
    (None = ESI sent none)."""

    def __init__(self, ttl: timedelta | None = timedelta(minutes=30)):
        self.ttl = ttl
        self.calls: list[str] = []

    def _answer(self, kind: str) -> list:
        self.calls.append(kind)
        header = None if self.ttl is None else _http_date(datetime.now(timezone.utc) + self.ttl)
        esi_client._note_last_modified(_Resp(header))
        return []

    def character_assets(self, character_id, auth_role):
        return self._answer("assets")

    def character_industry_jobs(self, character_id, auth_role):
        return self._answer("industry_jobs")

    def character_public_info(self, character_id):
        return {}

    def corporation_public_info(self, corporation_id):
        return {"name": str(corporation_id)}

    def resolve_names(self, ids):
        return {i: str(i) for i in ids}


def _next_fetch_after(kind: str = "assets"):
    return storage.list_esi_next_fetch_after().get(("character", ALICE, kind))


def _later(**delta) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**delta)


# ------------------------------------------------------------ collector

class _PagedResp:
    def __init__(self, payload, expires=None, last_modified=None, pages=2):
        self._payload = payload
        self.headers = {"X-Pages": str(pages)}
        if expires:
            self.headers["Expires"] = expires
        if last_modified:
            self.headers["Last-Modified"] = last_modified

    def json(self):
        return self._payload


def _paged_client(monkeypatch, by_page):
    def _fake(self, path, params=None, auth_role=None, retries=3):
        return by_page[params["page"]]
    monkeypatch.setattr(ESIClient, "_get_response", _fake)
    return ESIClient()


def test_collector_keeps_every_page_and_both_collectors_work_together(monkeypatch):
    early = "Mon, 16 Nov 2026 10:30:00 GMT"
    late = "Mon, 16 Nov 2026 10:45:00 GMT"
    modified = "Mon, 16 Nov 2026 10:00:00 GMT"
    client = _paged_client(monkeypatch, {
        1: _PagedResp([1], early, modified),
        2: _PagedResp([2], late, modified),
    })
    with collect_last_modified() as stamps, collect_expires() as expires:
        rows = client._get_all_pages("/characters/1/assets/", auth_role="esi:1")
    assert rows == [1, 2]
    assert [e.isoformat() for e in expires] == [
        "2026-11-16T10:30:00+00:00", "2026-11-16T10:45:00+00:00",
    ]
    assert len(stamps) == 2
    assert all(e.tzinfo is not None for e in expires)


class _SessionResp:
    status_code = 200

    def __init__(self, expires):
        self.headers = {"Expires": expires} if expires else {}

    def json(self):
        return {"x": 1}


def _session_client(monkeypatch, expires):
    client = ESIClient()
    monkeypatch.setattr(client, "_await_error_budget", lambda: None)
    monkeypatch.setattr(client, "_record_error_budget", lambda resp: None)
    monkeypatch.setattr(client.tokens, "auth_header", lambda role: {"Authorization": "Bearer x"})
    monkeypatch.setattr(client.session, "get", lambda *a, **k: _SessionResp(expires))
    return client


def test_collector_ignores_missing_garbled_public_and_ignored_reads(monkeypatch):
    with collect_expires() as expires:
        _session_client(monkeypatch, "not a date")._get("/characters/1/wallet/", auth_role="esi:1")
        _session_client(monkeypatch, None)._get("/characters/1/wallet/", auth_role="esi:1")
        _session_client(monkeypatch, "Mon, 16 Nov 2026 10:30:00 GMT")._get("/universe/types/34/")
        with esi_client.ignore_last_modified():
            _session_client(monkeypatch, "Mon, 16 Nov 2026 10:30:00 GMT")._get(
                "/characters/1/wallet/", auth_role="esi:1")
    assert expires == []
    with collect_expires() as expires:
        _session_client(monkeypatch, "Mon, 16 Nov 2026 10:30:00 GMT")._get(
            "/characters/1/wallet/", auth_role="esi:1")
    assert [e.isoformat() for e in expires] == ["2026-11-16T10:30:00+00:00"]


# ------------------------------------------------------------ storage

def test_success_stores_latest_expires_across_pages_capped(tenant):
    soon, later = _later(minutes=10), _later(minutes=40)
    orchestrator._record_success("character", ALICE, "assets", [], [soon, later, soon])
    assert _next_fetch_after() == later.replace(microsecond=later.microsecond)
    # A bogus far-future header cannot park the pair for good.
    orchestrator._record_success("character", ALICE, "assets", [], [_later(days=300)])
    assert _next_fetch_after() < _later(hours=25)


def test_success_without_header_clears_and_failure_keeps_it(tenant):
    orchestrator._record_success("character", ALICE, "assets", [], [_later(minutes=30)])
    assert _next_fetch_after() is not None
    storage.upsert_esi_freshness("character", ALICE, "assets", success=False, error="HTTP 500")
    assert _next_fetch_after() is not None  # a failure leaves the bound alone
    orchestrator._record_success("character", ALICE, "assets", [], [])
    assert _next_fetch_after() is None  # no header: today's behaviour


# ------------------------------------------------------------ scheduler

def test_not_due_before_expires_then_due_after(tenant, monkeypatch):
    monkeypatch.setattr(orchestrator, "_tier_hours", lambda kind: 0.0)  # tier never blocks
    _share("assets")
    _token()
    client = HeaderClient(timedelta(minutes=30))
    assert do_sync_due(client=client)["ok"] is True
    assert client.calls == ["assets"]
    assert _next_fetch_after() is not None

    now = datetime.now(timezone.utc)
    assert orchestrator.pending_due(now=now + timedelta(minutes=29)) == []
    assert do_sync_due(client=client, now=now + timedelta(minutes=29))["owners"] == []
    assert client.calls == ["assets"]
    due = orchestrator.pending_due(now=now + timedelta(minutes=31))
    assert [(r[0], r[1], r[2]) for r in due] == [("character", ALICE, "assets")]
    do_sync_due(client=client, now=now + timedelta(minutes=31))
    assert client.calls == ["assets", "assets"]


def test_tier_interval_stays_a_lower_bound(tenant, monkeypatch):
    monkeypatch.setattr(orchestrator, "_tier_hours", lambda kind: 6.0)
    _share("assets")
    _token()
    do_sync_due(client=HeaderClient(timedelta(minutes=30)))
    now = datetime.now(timezone.utc)
    # Expires has passed, the 6 h tier has not.
    assert orchestrator.pending_due(now=now + timedelta(hours=2)) == []
    assert orchestrator.pending_due(now=now + timedelta(hours=6, minutes=1)) != []


def test_no_header_behaves_as_before(tenant, monkeypatch):
    monkeypatch.setattr(orchestrator, "_tier_hours", lambda kind: 0.0)
    _share("assets")
    _token()
    client = HeaderClient(None)
    do_sync_due(client=client)
    assert _next_fetch_after() is None
    assert orchestrator.pending_due() != []
    do_sync_due(client=client)
    assert client.calls == ["assets", "assets"]


# ------------------------------------------------------------ manual syncs

def test_manual_sync_before_expires_is_skipped_with_message(tenant):
    _share("assets")
    _share("industry_jobs")
    _token()
    first = HeaderClient(timedelta(minutes=30))
    assert do_sync_for_tool("production", client=first)["ok"] is True
    assert sorted(first.calls) == ["assets", "industry_jobs"]

    second = HeaderClient(timedelta(minutes=30))
    result = do_sync_for_tool("production", client=second)
    assert second.calls == []  # nothing fetched
    assert result["ok"] is True
    assert sorted(u["data_kind"] for u in result["unchanged"]) == ["assets", "industry_jobs"]
    assert "unchanged until" in result["unchanged_message"]
    assert "2 data sets" in result["unchanged_message"]
    assert result["unchanged"][0]["until"].endswith("+00:00")

    # do_sync_all takes the same rule.
    assert do_sync_all(client=second)["unchanged"]
    assert second.calls == []


def test_manual_sync_fetches_a_pair_without_expires_and_skips_only_the_held_one(tenant):
    _share("assets")
    _share("industry_jobs")
    _token()
    storage.upsert_esi_freshness(
        "character", ALICE, "assets", success=True, next_fetch_after=_later(minutes=20),
    )
    client = HeaderClient(None)
    result = do_sync_for_tool("production", client=client)
    assert client.calls == ["industry_jobs"]
    assert [u["data_kind"] for u in result["unchanged"]] == ["assets"]


def test_manual_sync_after_expires_fetches_and_reports_nothing_unchanged(tenant):
    _share("assets")
    _token()
    storage.upsert_esi_freshness(
        "character", ALICE, "assets", success=True, next_fetch_after=_later(minutes=-1),
    )
    client = HeaderClient(None)
    result = do_sync_for_tool("production", client=client)
    assert client.calls == ["assets"]
    assert "unchanged" not in result and "unchanged_message" not in result


# ------------------------------------------------------------ ESI call counts

class _TtlClient(HeaderClient):
    """Assets cached by ESI for 60 min, jobs for 5 min, expressed on the
    simulated clock `sim_now` the loop below advances."""

    sim_now: datetime

    def _answer(self, kind: str) -> list:
        self.calls.append(kind)
        ttl = timedelta(minutes=60 if kind == "assets" else 5)
        esi_client._note_last_modified(_Resp(_http_date(self.sim_now + ttl)))
        return []


@pytest.mark.parametrize("tier_hours", [6.0, 0.1])
def test_call_counts_of_a_scheduler_day_with_and_without_the_floor(
    tenant, monkeypatch, capsys, tier_hours,
):
    """12 h of 5-minute scheduler ticks. "Without" switches the floor off, which
    is exactly the behaviour before this change."""
    monkeypatch.setattr(orchestrator, "_tier_hours", lambda kind: tier_hours)
    real_floor = orchestrator._expires_floor
    floor_on = {"value": True}
    monkeypatch.setattr(
        orchestrator, "_expires_floor",
        lambda expires, now=None: real_floor(expires, now) if floor_on["value"] else None,
    )

    def simulate(enabled: bool) -> int:
        pg_helpers.wipe_tables("esi_freshness", "character_assets", "character_industry_jobs")
        floor_on["value"] = enabled
        _share("assets")
        _share("industry_jobs")
        _token()
        client = _TtlClient()
        start = datetime.now(timezone.utc)
        for step in range(12 * 12):
            client.sim_now = start + timedelta(minutes=5 * step, seconds=30)
            before = len(client.calls)
            do_sync_due(client=client, now=client.sim_now)
            # The orchestrator stamps wall-clock time; move what this tick
            # fetched onto the simulated timeline.
            for kind in set(client.calls[before:]):
                with storage.connect() as conn:
                    conn.execute(
                        "UPDATE esi_freshness SET last_success_at = ?, last_attempt_at = ?, "
                        "data_as_of = ? WHERE data_kind = ?",
                        (client.sim_now, client.sim_now, client.sim_now, kind),
                    )
        return len(client.calls)

    without = simulate(False)
    with_floor = simulate(True)
    with capsys.disabled():
        print(f"ESI calls over 12 h (tier {tier_hours} h): without Expires {without}, with {with_floor}")
    assert with_floor <= without
    if tier_hours < 0.5:
        assert with_floor < without


def test_character_sync_summary_carries_the_unchanged_message_and_overview_the_tier():
    from eve_trader.character_management import fields

    plain = fields.summarise_sync({"ok": True, "owners": []})
    assert "unchanged" not in plain and "unchanged_message" not in plain
    held = fields.summarise_sync({
        "ok": True, "owners": [], "unchanged": [{"data_kind": "skills"}],
        "unchanged_message": "1 data set unchanged until 14:32 UTC (ESI serves the cached copy until then)",
    })
    assert held["unchanged"] == [{"data_kind": "skills"}]
    assert "unchanged until" in held["unchanged_message"]
    assert fields.sync_max_age_hours() == 6.0  # TradingConfig default, the normal tier

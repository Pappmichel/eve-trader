"""The data age shown for synced ESI data is ESI's own `Last-Modified`, not
the fetch time: ESI serves owner endpoints from a cache (assets 60 min,
orders 20 min, ...), so a re-fetch inside that window gets the same data."""
from datetime import datetime, timezone

from eve_trader import portfolio, storage
from eve_trader.esi_client import ESIClient, collect_last_modified
from eve_trader.esi_data import orchestrator


class _Resp:
    status_code = 200

    def __init__(self, payload, last_modified=None, total_pages=1):
        self._payload = payload
        self.headers = {"X-Pages": str(total_pages)}
        if last_modified:
            self.headers["Last-Modified"] = last_modified

    def json(self):
        return self._payload


def _client_returning(monkeypatch, responses):
    """ESIClient whose session.get returns `responses[page]` (page 1 when
    the request has no page param)."""
    client = ESIClient()
    monkeypatch.setattr(client, "_await_error_budget", lambda: None)
    monkeypatch.setattr(client, "_record_error_budget", lambda resp: None)
    monkeypatch.setattr(client.tokens, "auth_header", lambda role: {"Authorization": "Bearer x"})

    def _get(url, params=None, headers=None, timeout=None):
        return responses[(params or {}).get("page", 1)]
    monkeypatch.setattr(client.session, "get", _get)
    return client


def test_collects_last_modified_of_every_authenticated_page(monkeypatch):
    client = _client_returning(monkeypatch, {
        1: _Resp([1], "Mon, 19 Oct 2026 15:05:37 GMT", total_pages=2),
        2: _Resp([2], "Mon, 19 Oct 2026 14:58:00 GMT", total_pages=2),
    })

    with collect_last_modified() as seen:
        rows = client._get_all_pages("/characters/1/assets/", auth_role="esi:1")

    assert rows == [1, 2]
    assert sorted(seen) == [
        datetime(2026, 10, 19, 14, 58, tzinfo=timezone.utc),
        datetime(2026, 10, 19, 15, 5, 37, tzinfo=timezone.utc),
    ]


def test_public_reads_and_reads_outside_a_collector_are_not_collected(monkeypatch):
    client = _client_returning(monkeypatch, {1: _Resp({"x": 1}, "Mon, 19 Oct 2026 15:05:37 GMT")})

    client._get("/characters/1/wallet/", auth_role="esi:1")  # no collector: no-op
    with collect_last_modified() as seen:
        client._get("/universe/types/34/")  # public, not owner data
    assert seen == []


def test_missing_or_garbled_header_is_ignored(monkeypatch):
    client = _client_returning(monkeypatch, {1: _Resp({"x": 1}, "not a date")})
    with collect_last_modified() as seen:
        client._get("/characters/1/wallet/", auth_role="esi:1")
    assert seen == []


def test_success_records_oldest_last_modified_and_failed_member_adds_nothing(monkeypatch):
    stored = []
    monkeypatch.setattr(
        storage, "upsert_esi_freshness",
        lambda owner_type, owner_id, kind, **kw: stored.append((owner_type, owner_id, kind, kw)),
    )
    old = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
    new = datetime(2026, 10, 19, 15, 30, tzinfo=timezone.utc)
    failed_member = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

    def _ok(client, owner_id, auth_role, owner_name, **_kw):
        from eve_trader import esi_client
        esi_client._last_modified_seen.get().extend([new, old])
        return 3

    def _fails(client, owner_id, auth_role, owner_name, **_kw):
        from eve_trader import esi_client
        esi_client._last_modified_seen.get().append(failed_member)
        raise orchestrator.ESIError("HTTP 403")

    ages: list = []
    monkeypatch.setattr(orchestrator, "fetcher_for", lambda kind, owner_type: _fails)
    try:
        orchestrator._run_kind(
            client=None, data_kind="assets", owner_type="corporation", owner_id=9,
            auth_role="esi:1", owner_name="Corp", extra={}, data_ages=ages,
        )
    except orchestrator.ESIError:
        pass
    monkeypatch.setattr(orchestrator, "fetcher_for", lambda kind, owner_type: _ok)
    wrote = orchestrator._run_kind(
        client=None, data_kind="assets", owner_type="corporation", owner_id=9,
        auth_role="esi:2", owner_name="Corp", extra={}, data_ages=ages,
    )
    orchestrator._record_success("corporation", 9, "assets", ages)

    assert wrote == 3
    assert stored == [("corporation", 9, "assets", {"success": True, "data_as_of": old})]


def test_success_without_header_stores_no_data_age(monkeypatch):
    stored = []
    monkeypatch.setattr(
        storage, "upsert_esi_freshness",
        lambda owner_type, owner_id, kind, **kw: stored.append(kw),
    )
    orchestrator._record_success("character", 1, "wallet_balance", [])
    assert stored == [{"success": True, "data_as_of": None}]


def test_portfolio_sync_times_show_data_age_not_fetch_time(monkeypatch):
    monkeypatch.setattr(storage, "list_esi_freshness", lambda: [
        # fetched 15:52, but ESI generated the data at 15:05
        ("character", 1, "assets", "2026-10-19T15:52:00+00:00", "2026-10-19T15:52:00+00:00", None,
         "2026-10-19T15:05:37+00:00"),
        ("character", 2, "assets", "2026-10-19T15:40:00+00:00", "2026-10-19T15:40:00+00:00", None,
         "2026-10-19T15:10:00+00:00"),
        # attempted, never succeeded: no data age
        ("character", 3, "assets", None, "2026-10-19T15:50:00+00:00", "HTTP 403", None),
    ])
    shares = {kind: ([], []) for kind in ("assets", "blueprints", "market_orders", "contracts", "wallet_balance")}
    shares["assets"] = ([1, 2, 3], [])

    times = portfolio._sync_times(shares, escrow_known=False)

    assert times["assets"] == "2026-10-19T15:10:00+00:00"
    assert times["wallet"] is None

"""An empty assets/blueprints list after a large stock is reloaded once and
rejected; the next attempt accepts it. Other kinds are untouched."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.esi_client import ESIError
from eve_trader.esi_data import fetchers

from . import pg_helpers
from .pg_helpers import tenant  # noqa: F401

pytestmark = pg_helpers.postgres_required()

OWNER = 940001


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("esi_freshness", "character_assets", "character_blueprints",
                           "character_industry_jobs")
    yield


def _asset(n: int) -> dict:
    return {"item_id": n, "type_id": 34, "location_id": 60003760, "location_flag": "Hangar",
            "quantity": 1, "is_singleton": False}


def _assets(n: int) -> list[dict]:
    return [_asset(i) for i in range(1, n + 1)]


class _Client:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def character_assets(self, owner_id, auth_role=None):
        self.calls += 1
        return self.answers.pop(0)

    def character_asset_names(self, *a, **k):
        return []


@pytest.fixture
def observed(monkeypatch):
    seen = []
    monkeypatch.setattr(fetchers, "_archive_stock_observation", lambda *a: seen.append(a))
    return seen


def _stored() -> int:
    return storage.count_owner_snapshot_rows("character_assets", OWNER)


def _fetch(client):
    return fetchers.fetch_character_assets(client, OWNER, "x:1", "Alice")


def _seed(n):
    storage.replace_assets("character_assets", fetchers._asset_rows(_assets(n), "Alice"),
                           owner_character_id=OWNER, owner_name="Alice")


def test_empty_twice_after_large_stock_fails_and_keeps_rows(tenant, observed):
    _seed(1500)
    client = _Client([], [])
    with pytest.raises(ESIError, match="^suspicious_empty:"):
        _fetch(client)
    assert client.calls == 2
    assert _stored() == 1500
    assert observed == []


def test_empty_then_full_writes_new_state(tenant, observed):
    _seed(1500)
    client = _Client([], _assets(60))
    _fetch(client)
    assert _stored() == 60
    assert len(observed) == 1


def test_previous_attempt_with_prefix_accepts_empty(tenant, observed):
    _seed(1500)
    storage.upsert_esi_freshness("character", OWNER, "assets", success=False,
                                 error="suspicious_empty: ESI returned no assets")
    client = _Client([])
    _fetch(client)
    assert client.calls == 1
    assert _stored() == 0


def test_small_previous_stock_accepts_empty_at_once(tenant, observed):
    _seed(30)
    client = _Client([])
    _fetch(client)
    assert client.calls == 1
    assert _stored() == 0


def test_industry_jobs_empty_after_many_rows_is_accepted(tenant, monkeypatch):
    rows = [(i, 1, 100, 200, 1, 5, "active", "2026-11-16T00:00:00+00:00", None, 1, "x", None)
            for i in range(1, 61)]
    storage.replace_industry_jobs("character_industry_jobs", rows, owner_character_id=OWNER)
    monkeypatch.setattr(fetchers, "_archive_industry_jobs", lambda *a, **k: None)

    class C:
        def character_industry_jobs(self, *a, **k):
            return []

    assert fetchers.fetch_character_industry_jobs(C(), OWNER, "x:1", "Alice") == {"written": 0}

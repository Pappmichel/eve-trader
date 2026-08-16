from eve_trader import storage
from eve_trader.production import actions as production_actions
from eve_trader.production import sde
from eve_trader.production.config import ProductionConfig


def test_sde_refresh_state_round_trips(tmp_path):
    db_path = tmp_path / "test.db"
    assert storage.get_sde_refresh_state(db_path=db_path) is None

    storage.set_sde_refresh_state("2026-07-16T10:00:00+00:00", "etag-1", db_path=db_path)
    assert storage.get_sde_refresh_state(db_path=db_path) == ("2026-07-16T10:00:00+00:00", "etag-1")

    # A second call replaces, not appends (single-row table).
    storage.set_sde_refresh_state("2026-07-17T10:00:00+00:00", "etag-2", db_path=db_path)
    assert storage.get_sde_refresh_state(db_path=db_path) == ("2026-07-17T10:00:00+00:00", "etag-2")


def test_candidate_universe_built_at_returns_latest_run_ts(tmp_path):
    db_path = tmp_path / "test.db"
    assert storage.get_candidate_universe_built_at(db_path=db_path) is None

    with storage.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO candidate_universe (run_ts, item, type_id, volume_m3, category, market_group_path, meta_level) "
            "VALUES (?, 'Widget', 1, 1.0, 'Material', 'Path', NULL)",
            ("2026-07-15T10:00:00",),
        )
        conn.execute(
            "INSERT INTO candidate_universe (run_ts, item, type_id, volume_m3, category, market_group_path, meta_level) "
            "VALUES (?, 'Gadget', 2, 1.0, 'Material', 'Path', NULL)",
            ("2026-07-16T10:00:00",),
        )

    assert storage.get_candidate_universe_built_at(db_path=db_path) == "2026-07-16T10:00:00"


class _FakeHeadResponse:
    def __init__(self, etag):
        self.headers = {"ETag": etag} if etag else {}

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc

    def head(self, url, timeout=None):
        if self.exc is not None:
            raise self.exc
        return self.response


def test_dump_etag_returns_header_value():
    session = _FakeSession(response=_FakeHeadResponse("some-etag"))
    assert sde._dump_etag(session, "https://example.com/") == "some-etag"


def test_dump_etag_returns_none_on_request_failure():
    import requests
    session = _FakeSession(exc=requests.RequestException("boom"))
    assert sde._dump_etag(session, "https://example.com/") is None


def test_check_for_newer_sde_detects_etag_mismatch(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_refresh_state", lambda: ("2026-07-15T10:00:00+00:00", "old-etag"))
    monkeypatch.setattr(sde, "_dump_etag", lambda session, base_url: "new-etag")

    result = sde.check_for_newer_sde(ProductionConfig())

    assert result["newer_sde_available"] is True
    assert result["remote_check_succeeded"] is True
    assert result["local_refreshed_at"] == "2026-07-15T10:00:00+00:00"


def test_check_for_newer_sde_no_mismatch_when_etags_match(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_refresh_state", lambda: ("2026-07-15T10:00:00+00:00", "same-etag"))
    monkeypatch.setattr(sde, "_dump_etag", lambda session, base_url: "same-etag")

    result = sde.check_for_newer_sde(ProductionConfig())

    assert result["newer_sde_available"] is False


def test_check_for_newer_sde_never_refreshed_reports_not_newer(monkeypatch):
    # Nothing to compare against yet - must not nag on a guess.
    monkeypatch.setattr(storage, "get_sde_refresh_state", lambda: None)
    monkeypatch.setattr(sde, "_dump_etag", lambda session, base_url: "some-etag")

    result = sde.check_for_newer_sde(ProductionConfig())

    assert result["newer_sde_available"] is False
    assert result["local_refreshed_at"] is None


def test_check_for_newer_sde_remote_unreachable_reports_not_newer(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_refresh_state", lambda: ("2026-07-15T10:00:00+00:00", "old-etag"))
    monkeypatch.setattr(sde, "_dump_etag", lambda session, base_url: None)

    result = sde.check_for_newer_sde(ProductionConfig())

    assert result["newer_sde_available"] is False
    assert result["remote_check_succeeded"] is False


def test_do_check_sde_freshness_flags_stale_trading_universe(monkeypatch):
    monkeypatch.setattr(sde, "check_for_newer_sde", lambda cfg: {
        "local_refreshed_at": "2026-07-16T10:00:00+00:00",
        "remote_check_succeeded": True, "newer_sde_available": False,
    })
    monkeypatch.setattr(storage, "get_sde_refresh_state", lambda: ("2026-07-16T10:00:00+00:00", "etag"))
    # Trading's universe was built the day BEFORE the SDE was last refreshed.
    monkeypatch.setattr(storage, "get_candidate_universe_built_at", lambda: "2026-07-15T09:00:00")

    result = production_actions.do_check_sde_freshness()

    assert result["trading_universe_stale"] is True


def test_do_check_sde_freshness_not_stale_when_universe_rebuilt_after_sde(monkeypatch):
    monkeypatch.setattr(sde, "check_for_newer_sde", lambda cfg: {
        "local_refreshed_at": "2026-07-16T10:00:00+00:00",
        "remote_check_succeeded": True, "newer_sde_available": False,
    })
    monkeypatch.setattr(storage, "get_sde_refresh_state", lambda: ("2026-07-16T10:00:00+00:00", "etag"))
    monkeypatch.setattr(storage, "get_candidate_universe_built_at", lambda: "2026-07-16T11:00:00")

    result = production_actions.do_check_sde_freshness()

    assert result["trading_universe_stale"] is False


def test_do_check_sde_freshness_not_stale_when_nothing_has_run_yet(monkeypatch):
    monkeypatch.setattr(sde, "check_for_newer_sde", lambda cfg: {
        "local_refreshed_at": None, "remote_check_succeeded": True, "newer_sde_available": False,
    })
    monkeypatch.setattr(storage, "get_sde_refresh_state", lambda: None)
    monkeypatch.setattr(storage, "get_candidate_universe_built_at", lambda: None)

    result = production_actions.do_check_sde_freshness()

    assert result["trading_universe_stale"] is False

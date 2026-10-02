"""data_versions.do_data_versions (FRONTEND_PLAN.md B.12)."""
import datetime as dt

from eve_trader import data_versions, storage
from eve_trader.production import jita_price_cache


def test_versions_reflect_each_source(monkeypatch):
    monkeypatch.setattr(storage, "newest_esi_freshness_success_at", lambda: dt.datetime(2026, 10, 3, 1, 2, 3))
    monkeypatch.setattr(storage, "get_esi_sync_time", lambda scope: "2026-10-03T00:00:00" if scope == "trading" else None)
    monkeypatch.setattr(storage, "latest_portfolio_snapshot_date", lambda: dt.date(2026, 10, 3))
    monkeypatch.setattr(data_versions, "_latest_shortlist_run", lambda: "2026-10-02T12:47:02")
    monkeypatch.setattr(jita_price_cache, "last_updated_at", lambda: None)

    assert data_versions.do_data_versions() == {
        "esi": "2026-10-03 01:02:03",
        "trading_pipeline": "2026-10-03T00:00:00",
        "shortlist": "2026-10-02T12:47:02",
        "portfolio": "2026-10-03",
        "jita_prices": None,
    }

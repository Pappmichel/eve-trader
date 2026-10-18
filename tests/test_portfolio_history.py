"""Portfolio realized-history buckets each tool's summary by_day."""
from eve_trader import portfolio
from eve_trader.actions import ActionError


def _summary(by_day, *, empty=False, start="2026-10-01"):
    return {
        "status": {"empty": empty, "needs_rebuild": empty},
        "by_day": by_day,
        "period": {"start": start, "end": "2026-10-18", "since_t0": False, "days": 30},
    }


def test_history_uses_day_buckets_and_skips_missing_days(monkeypatch):
    def production(**_kw):
        return _summary([
            {"date": "2026-10-01T00:00:00+00:00", "profit": 10},
            {"date": "2026-10-03", "profit": 5},
        ])

    def trading(**_kw):
        return _summary([{"date": "2026-10-01", "profit": 2}])

    monkeypatch.setattr("eve_trader.production.realized.report.summary", production)
    monkeypatch.setattr("eve_trader.trade_realized.report.summary", trading)

    history = portfolio.realized_history(["production", "trading"], period="30")

    assert history["bucket"] == "day"
    assert history["channels"] == ["production", "trading"]
    assert "doctrine" not in history["channels"]
    assert [row["date"] for row in history["rows"]] == ["2026-10-01", "2026-10-03"]
    assert history["rows"][0] == {
        "date": "2026-10-01", "production": 10.0, "trading": 2.0, "total": 12.0, "cumulative": 12.0,
    }
    assert history["rows"][1]["trading"] == 0.0
    assert history["rows"][1]["cumulative"] == 17.0


def test_history_week_buckets_sum_a_monday_week(monkeypatch):
    def production(**_kw):
        return _summary([
            {"date": "2026-10-01", "profit": 10},  # Thursday
            {"date": "2026-10-03", "profit": 5},   # Saturday
            {"date": "2026-10-05", "profit": 1},   # next Monday
        ])

    monkeypatch.setattr("eve_trader.production.realized.report.summary", production)

    history = portfolio.realized_history(["production"], period="30", bucket="week")

    assert history["bucket"] == "week"
    assert [row["date"] for row in history["rows"]] == ["2026-09-28", "2026-10-05"]
    assert history["rows"][0]["production"] == 15.0
    assert history["rows"][0]["cumulative"] == 15.0
    assert history["rows"][1]["cumulative"] == 16.0


def test_since_t0_longer_than_90_days_uses_weeks(monkeypatch):
    def production(**_kw):
        return _summary(
            [{"date": "2026-01-02", "profit": 3}, {"date": "2026-01-03", "profit": 4}],
            start="2000-01-01",
        )

    monkeypatch.setattr("eve_trader.production.realized.report.summary", production)
    history = portfolio.realized_history(["production"], period="since_t0")
    assert history["bucket"] == "week"
    assert history["rows"][0]["production"] == 7.0


def test_a_tool_that_needs_a_recompute_adds_no_days(monkeypatch):
    def production(**_kw):
        return _summary([{"date": "2026-10-01", "profit": 10}])

    def trading(**_kw):
        return _summary([{"date": "2026-10-02", "profit": 99}], empty=True)

    monkeypatch.setattr("eve_trader.production.realized.report.summary", production)
    monkeypatch.setattr("eve_trader.trade_realized.report.summary", trading)

    history = portfolio.realized_history(["production", "trading"])
    assert history["channels"] == ["production", "trading"]
    assert len(history["rows"]) == 1
    assert history["rows"][0]["trading"] == 0.0
    assert history["rows"][0]["total"] == 10.0


def test_history_rejects_an_unknown_bucket():
    try:
        portfolio.realized_history([], bucket="month")
    except ActionError as exc:
        assert "bucket" in str(exc)
    else:
        raise AssertionError("expected ActionError")

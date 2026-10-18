"""Portfolio realized-profit cards sum the granted tools' summary figures."""
from eve_trader import portfolio
from eve_trader.actions import ActionError


def _summary(**overrides):
    data = {
        "status": {"empty": False, "needs_rebuild": False},
        "realized_profit": 0.0,
        "revenue": 0.0,
        "margin": None,
        "coverage": 1.0,
        "estimated": False,
        "sale_count": 1,
    }
    data.update(overrides)
    return data


def test_realized_cards_sum_ready_tools_and_skip_an_empty_one(monkeypatch):
    seen = []

    def production(**kw):
        seen.append(("production", kw))
        return _summary(realized_profit=10.0, revenue=40.0, margin=0.25, sale_count=2)

    def trading(**_kw):
        return _summary(
            status={"empty": True, "needs_rebuild": True},
            realized_profit=0.0, coverage=None, estimated=False, sale_count=0,
        )

    def doctrine(**_kw):
        data = _summary(realized_profit=65.0, coverage=0.5, estimated=True, contracts=3)
        data.pop("sale_count")
        return data

    def station(**_kw):
        return _summary(realized_profit=4.0)

    monkeypatch.setattr("eve_trader.production.realized.report.summary", production)
    monkeypatch.setattr("eve_trader.trade_realized.report.summary", trading)
    monkeypatch.setattr("eve_trader.doctrine.realized.summary", doctrine)
    monkeypatch.setattr("eve_trader.station_trading.realized.summary", station)

    cards = portfolio.realized_cards(["production", "trading", "doctrine"])
    assert cards["total"] == 75.0
    assert cards["period"] == "30"
    by_tool = {row["tool"]: row for row in cards["rows"]}
    assert by_tool["trading"]["profit"] is None
    assert by_tool["trading"]["sales_count"] is None
    assert by_tool["doctrine"]["estimated"] is True
    assert by_tool["doctrine"]["sales_count"] == 3
    assert by_tool["production"]["revenue"] == 40.0
    assert by_tool["production"]["link"] == "/production/realized"
    assert seen[0][1]["since_t0"] is False
    assert seen[0][1]["days"] == 30

    one = portfolio.realized_cards(["doctrine"])
    assert one["total"] == 65.0
    assert one["rows"][0]["profit"] == 65.0

    both = portfolio.realized_cards(["production", "station_trading"])
    assert both["total"] == 14.0
    assert {row["tool"] for row in both["rows"]} == {"production", "station_trading"}


def test_realized_cards_periods_and_since_t0_uses_the_ledger_window(monkeypatch):
    captured = []

    def reader(**kw):
        captured.append(kw)
        return _summary(realized_profit=1.0)

    monkeypatch.setattr("eve_trader.production.realized.report.summary", reader)
    monkeypatch.setattr("eve_trader.trade_realized.report.summary", reader)
    monkeypatch.setattr("eve_trader.doctrine.realized.summary", reader)
    monkeypatch.setattr("eve_trader.station_trading.realized.summary", reader)

    for period, days in (("7", 7), ("30", 30), ("90", 90)):
        captured.clear()
        portfolio.realized_cards(["trading"], period=period)
        assert captured[0]["days"] == days
        assert captured[0]["since_t0"] is False
        assert captured[0]["start"] is not None

    captured.clear()
    portfolio.realized_cards(["production"], period="since_t0")
    assert captured[0]["since_t0"] is True
    assert captured[0]["start"] is None
    assert captured[0]["days"] is None


def test_realized_cards_freight_sums_only_tools_with_a_figure(monkeypatch):
    def production(**_kw):
        return _summary(
            realized_profit=10.0,
            cost_structure=[
                {"key": "freight_courier", "amount": 3},
                {"key": "freight_configured", "amount": 1},
            ],
        )

    def trading(**_kw):
        return _summary(
            status={"empty": True, "needs_rebuild": True},
            realized_profit=0.0,
            cost_structure=[
                {"key": "freight_courier", "amount": 99},
                {"key": "freight_configured", "amount": 99},
            ],
        )

    def doctrine(**_kw):
        data = _summary(realized_profit=1.0, freight_from_couriers=2, freight_configured=4)
        data.pop("sale_count")
        data["contracts"] = 1
        return data

    def station(**_kw):
        return _summary(realized_profit=4.0)

    monkeypatch.setattr("eve_trader.production.realized.report.summary", production)
    monkeypatch.setattr("eve_trader.trade_realized.report.summary", trading)
    monkeypatch.setattr("eve_trader.doctrine.realized.summary", doctrine)
    monkeypatch.setattr("eve_trader.station_trading.realized.summary", station)

    cards = portfolio.realized_cards(["production", "trading", "doctrine"])
    assert cards["freight"] == {"courier": 5.0, "configured": 5.0}

    empty = portfolio.realized_cards(["trading"])
    assert empty["freight"] is None

    one = portfolio.realized_cards(["station_trading"])
    assert one["freight"] == {"courier": 0.0, "configured": 0.0}


def test_realized_cards_rejects_an_unknown_period():
    try:
        portfolio.realized_cards(["trading"], period="14")
    except ActionError as exc:
        assert "period" in str(exc)
    else:
        raise AssertionError("expected ActionError")

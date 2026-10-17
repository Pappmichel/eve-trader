"""Portfolio realized-profit cards sum the granted tools' API figures."""
from eve_trader import portfolio


def test_realized_cards_sum_ready_tools_and_skip_an_empty_one(monkeypatch):
    def production(**_kw):
        return {
            "status": {"empty": False, "needs_rebuild": False},
            "realized_profit": 10.0, "coverage": 1.0, "estimated": False,
        }

    def trading(**_kw):
        return {
            "status": {"empty": True, "needs_rebuild": True},
            "realized_profit": 0.0, "coverage": None, "estimated": False,
        }

    def doctrine(**_kw):
        return {
            "status": {"empty": False, "needs_rebuild": False},
            "realized_profit": 65.0, "coverage": 0.5, "estimated": True,
        }

    monkeypatch.setattr("eve_trader.production.realized.report.summary", production)
    monkeypatch.setattr("eve_trader.trade_realized.report.summary", trading)
    monkeypatch.setattr("eve_trader.doctrine.realized.summary", doctrine)

    cards = portfolio.realized_cards(["production", "trading", "doctrine"])
    assert cards["total"] == 75.0
    by_tool = {row["tool"]: row for row in cards["rows"]}
    assert by_tool["trading"]["profit"] is None
    assert by_tool["doctrine"]["estimated"] is True

    one = portfolio.realized_cards(["doctrine"])
    assert one["total"] is None
    assert one["rows"][0]["profit"] == 65.0

"""Trading realized-profit report: pure helpers and the read-only comparison.

Postgres cases live in test_trading_realized_report.py.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.trade_realized import actions as trade_actions
from eve_trader.trade_realized import report


def test_population_volatility_needs_two_days():
    assert report._daily_volatility([]) is None
    assert report._daily_volatility([10.0]) is None
    assert report._daily_volatility([10.0, 30.0]) == pytest.approx(10.0)


def test_compare_trading_lines_up_old_rows_and_the_ledger(monkeypatch):
    now = datetime.now(timezone.utc)
    inside = (now - timedelta(days=2)).isoformat()
    outside = (now - timedelta(days=40)).isoformat()
    frame = pd.DataFrame([
        {
            "run_ts": "2026-01-01", "type_id": 34, "item": "Tritanium",
            "sell_date": inside, "matched_qty": 5, "realized_profit": 999.0,
        },
        {
            "run_ts": "2026-02-01", "type_id": 34, "item": "Tritanium",
            "sell_date": inside, "matched_qty": 2, "realized_profit": 20.0,
        },
        {
            "run_ts": "2026-02-01", "type_id": 35, "item": "Pyerite",
            "sell_date": outside, "matched_qty": 9, "realized_profit": 90.0,
        },
        {
            "run_ts": "2026-02-01", "type_id": 36, "item": "Mexallon",
            "sell_date": inside, "matched_qty": 1, "realized_profit": 5.0,
        },
    ])
    monkeypatch.setattr(storage, "read_table", lambda table: frame)
    monkeypatch.setattr(storage, "sde_type_names", lambda type_ids: {})
    monkeypatch.setattr(report, "items", lambda **_kw: {
        "needs_rebuild": False,
        "rows": [{
            "type_id": 34, "type_name": "Tritanium",
            "units_sold": 2.0, "profit": 15.0,
        }],
    })

    result = report.compare_trading(30)

    assert result["needs_rebuild"] is False
    by_type = {row["type_id"]: row for row in result["rows"]}
    assert by_type[34]["old_profit"] == pytest.approx(20.0)
    assert by_type[34]["old_units"] == pytest.approx(2.0)
    assert by_type[34]["new_profit"] == pytest.approx(15.0)
    assert 35 not in by_type
    assert by_type[36]["new_profit"] == pytest.approx(0.0)
    assert result["old_profit"] == pytest.approx(25.0)
    assert result["new_profit"] == pytest.approx(15.0)


def test_compare_trading_rejects_a_bad_window():
    with pytest.raises(ActionError):
        trade_actions.do_compare_trading(0)

"""Capital aggregation (PORTFOLIO_OVERVIEW_PLAN.md 3.5)."""
from eve_trader.portfolio_capital import report


def _bucket(result, key):
    return next(row for row in result["buckets"] if row["key"] == key)


def test_listed_quantity_takes_the_oldest_layers_first():
    result = report(
        layers=[
            {"type_id": 34, "quantity": 5, "unit_cost": 10, "origin": "bought"},
            {"type_id": 34, "quantity": 10, "unit_cost": 20, "origin": "bought"},
        ],
        listed=[{"type_id": 34, "quantity": 7, "order_price": 30}],
        contracts=[],
        escrow=0.0,
        prices={34: 25},
    )
    listed = _bucket(result, "listed")
    stock = _bucket(result, "stock")
    assert listed["cost_basis"] == 5 * 10 + 2 * 20
    assert listed["market_value"] == 7 * 30
    assert listed["unrealized"] == listed["market_value"] - listed["cost_basis"]
    assert listed["comparison_value"] == 7 * 25
    assert stock["cost_basis"] == 8 * 20
    assert stock["market_value"] == 8 * 25
    assert stock["unrealized"] == stock["market_value"] - stock["cost_basis"]


def test_contract_items_take_the_layers_listed_orders_left():
    result = report(
        layers=[
            {"type_id": 34, "quantity": 5, "unit_cost": 10, "origin": "bought"},
            {"type_id": 34, "quantity": 10, "unit_cost": 20, "origin": "bought"},
        ],
        listed=[{"type_id": 34, "quantity": 4, "order_price": 30}],
        contracts=[{
            "contract_id": 1, "type_id": 34, "quantity": 3, "contract_price": 90,
        }],
        escrow=0.0,
        prices={34: 25},
    )
    assert _bucket(result, "listed")["cost_basis"] == 4 * 10
    assert _bucket(result, "contracts")["cost_basis"] == 1 * 10 + 2 * 20
    assert _bucket(result, "contracts")["market_value"] == 90
    assert _bucket(result, "stock")["cost_basis"] == 8 * 20


def test_missing_escrow_is_unknown_and_left_out_of_the_total():
    result = report(
        layers=[{"type_id": 34, "quantity": 2, "unit_cost": 10, "origin": "bought"}],
        listed=[],
        contracts=[],
        escrow=None,
        prices={34: 15},
    )
    escrow = _bucket(result, "escrow")
    assert escrow["cost_basis"] is None
    assert escrow["market_value"] is None
    assert escrow["unrealized"] is None
    assert result["total"]["market_value"] == 30.0
    assert result["total"]["cost_basis"] == 20.0
    assert result["total"]["unrealized"] == 10.0
    assert result["total"]["excluded"] == ["escrow"]


def test_opening_stock_is_estimated_and_still_has_unrealized_profit():
    result = report(
        layers=[{"type_id": 34, "quantity": 2, "unit_cost": 10, "origin": "opening"}],
        listed=[],
        contracts=[],
        escrow=0.0,
        prices={34: 15},
    )
    stock = _bucket(result, "stock")
    assert stock["estimated"] is True
    assert stock["coverage"] == 0.0
    assert stock["unrealized"] == 10.0
    assert result["total"]["unrealized"] == 10.0
    assert result["total"]["estimated"] is True
    assert "in_production" not in {row["key"] for row in result["buckets"]}


def test_running_jobs_show_their_cost_and_no_market_value():
    result = report(
        layers=[{"type_id": 34, "quantity": 2, "unit_cost": 10, "origin": "bought"}],
        listed=[],
        contracts=[],
        escrow=5.0,
        prices={34: 15},
        in_production={"count": 2, "cost": 100},
    )
    row = _bucket(result, "in_production")
    assert row["cost_basis"] == 100
    assert row["market_value"] is None
    assert row["unrealized"] is None
    # Running jobs keep their row but do not blank the total.
    assert result["total"]["cost_basis"] == 25.0
    assert result["total"]["market_value"] == 35.0
    assert result["total"]["unrealized"] == 10.0
    assert result["total"]["excluded"] == ["in_production"]


def test_unpriced_stock_is_left_out_of_both_sides():
    result = report(
        layers=[
            {"type_id": 34, "quantity": 2, "unit_cost": 10, "origin": "bought"},
            {"type_id": 35, "quantity": 4, "unit_cost": 8, "origin": "bought"},
        ],
        listed=[],
        contracts=[],
        escrow=0.0,
        prices={34: 15},
    )
    stock = _bucket(result, "stock")
    assert stock["cost_basis"] == 20.0
    assert stock["market_value"] == 30.0
    assert stock["unrealized"] == 10.0
    assert stock["coverage"] < 1

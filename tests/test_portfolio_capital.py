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


BP = 931700


def test_copy_runs_are_valued_per_run_not_at_the_original_quote():
    # 100 runs that cost 100,000 ISK; the original's quote is 50M per item,
    # a run is worth 1,200 (copy/invention cost).
    result = report(
        layers=[{"type_id": BP, "unit": "bpc_run", "quantity": 100, "unit_cost": 1_000, "origin": "produced"}],
        listed=[],
        contracts=[],
        escrow=0.0,
        prices={BP: 50_000_000},
        bpc_run_prices={BP: 1_200},
    )
    runs = _bucket(result, "bpc_runs")
    assert runs["label"] == "Blueprint copies"
    assert runs["quantity"] == 100
    assert runs["cost_basis"] == 100_000
    assert runs["market_value"] == 120_000
    assert runs["unrealized"] == 20_000
    assert _bucket(result, "stock")["quantity"] == 0
    assert result["total"]["market_value"] == 120_000


def test_a_sell_order_of_the_blueprint_type_does_not_draw_copy_runs():
    result = report(
        layers=[
            {"type_id": BP, "unit": "bpc_run", "quantity": 10, "unit_cost": 100, "origin": "produced"},
            {"type_id": BP, "unit": "item", "quantity": 1, "unit_cost": 1_000_000, "origin": "opening"},
        ],
        listed=[{"type_id": BP, "quantity": 1, "order_price": 2_000_000}],
        contracts=[],
        escrow=0.0,
        prices={BP: 1_500_000},
        bpc_run_prices={BP: 150},
    )
    assert _bucket(result, "listed")["cost_basis"] == 1_000_000
    assert _bucket(result, "listed")["coverage"] == 0.0  # the opening item, not a run
    runs = _bucket(result, "bpc_runs")
    assert runs["quantity"] == 10 and runs["cost_basis"] == 1_000
    assert _bucket(result, "stock")["quantity"] == 0


def test_copy_runs_without_a_value_are_left_out_of_both_sides():
    result = report(
        layers=[
            {"type_id": BP, "unit": "bpc_run", "quantity": 10, "unit_cost": 100, "origin": "produced"},
            {"type_id": BP + 1, "unit": "bpc_run", "quantity": 30, "unit_cost": 5, "origin": "produced"},
        ],
        listed=[],
        contracts=[],
        escrow=0.0,
        prices={},
        bpc_run_prices={BP: 150},
    )
    runs = _bucket(result, "bpc_runs")
    assert runs["quantity"] == 40
    assert runs["cost_basis"] == 1_000
    assert runs["market_value"] == 1_500
    assert runs["coverage"] == 10 / 40
    assert result["total"]["coverage"] == 10 / 40


def test_no_copy_runs_no_copy_bucket():
    result = report(
        layers=[{"type_id": 34, "quantity": 2, "unit_cost": 10, "origin": "bought"}],
        listed=[], contracts=[], escrow=0.0, prices={34: 15},
    )
    assert "bpc_runs" not in {row["key"] for row in result["buckets"]}


def _patch_capital_inputs(monkeypatch, layers, listed=()):
    from eve_trader.realized import access
    from eve_trader.realized import db as ledger_db

    monkeypatch.setattr(access, "shared_owners", lambda kind: [])
    monkeypatch.setattr(access, "shared_item_exchange_owners", lambda: [])
    monkeypatch.setattr(ledger_db, "list_remaining_layers", lambda: list(layers))
    monkeypatch.setattr(ledger_db, "open_sell_order_lines", lambda owners: list(listed))
    monkeypatch.setattr(ledger_db, "outstanding_priced_contract_items", lambda owners: [])
    monkeypatch.setattr(ledger_db, "open_buy_escrow_by_owner", lambda owners: [])
    monkeypatch.setattr(ledger_db, "running_job_cost", lambda: (0, 0.0))


def test_capital_action_values_copy_runs_with_the_per_run_value(monkeypatch):
    from eve_trader import portfolio

    # type_id, unit, quantity_left, unit_cost, origin, me, decryptor, estimated, missing, pool
    _patch_capital_inputs(monkeypatch, [
        (BP, "bpc_run", 100.0, 1_000.0, "produced", False, False, False, False, "global"),
        (34, "item", 2.0, 10.0, "bought", False, False, False, False, "global"),
    ], listed=[(BP, 1.0, 60_000_000.0)])
    asked = {}
    monkeypatch.setattr(portfolio, "_priced", lambda ids: asked.setdefault("items", set(ids)) and {34: 15.0, BP: 50e6})
    monkeypatch.setattr(portfolio, "_bpc_run_values",
                        lambda ids: asked.setdefault("runs", set(ids)) and {BP: 1_200.0})
    result = portfolio.do_get_portfolio_capital()
    assert asked == {"items": {34, BP}, "runs": {BP}}
    runs = _bucket(result, "bpc_runs")
    assert runs["market_value"] == 120_000 and runs["cost_basis"] == 100_000
    assert _bucket(result, "stock")["quantity"] == 2
    # The listed original drew no run layer: nothing covers it.
    assert _bucket(result, "listed")["cost_basis"] == 0

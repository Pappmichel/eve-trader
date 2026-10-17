"""Golden tests for the pure Production costing engine
(docs/PRODUCTION_REALIZED_PROFIT_PLAN.md, Phase 2). Hand-built ledgers with
the expected numbers worked out in comments. No database, no ESI."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from eve_trader.production.realized import costing as c
from eve_trader.production.realized.costing import (
    ESTIMATED_COVERAGE_THRESHOLD,
    CostComponents,
    IndustryJob,
    JobInput,
    JournalEntry,
    LedgerSnapshot,
    MarketOrder,
    WalletTransaction,
    rebuild_costing,
)

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
CHAR = 9001
OTHER_CHAR = 9002
CORP = 98000001
HOME = 1_000_000_000_001
JITA = 60003760
FORGE = 10000002


def at(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return T0 + timedelta(days=day - 1, hours=hour, minutes=minute)


_tx_ids = iter(range(1, 10_000))
_job_ids = iter(range(500_000, 600_000))


def buy(type_id, qty, price, when, *, location=HOME, rate=None, m3=None, owner=CHAR, tx_id=None):
    return WalletTransaction(
        owner_type="character", owner_id=owner, transaction_id=tx_id or next(_tx_ids), date=when,
        type_id=type_id, location_id=location, unit_price=price, quantity=qty, is_buy=True,
        region_id=FORGE if rate is not None else None, freight_rate_per_m3=rate, freight_m3=m3,
    )


def sell(type_id, qty, price, when, *, location=HOME, owner=CHAR, tx_id=None):
    return WalletTransaction(
        owner_type="character", owner_id=owner, transaction_id=tx_id or next(_tx_ids), date=when,
        type_id=type_id, location_id=location, unit_price=price, quantity=qty, is_buy=False,
    )


def job(activity, blueprint, product, runs, start, end, cost, inputs=(), *, status="delivered",
        licensed_runs=None, successful_runs=None, output_quantity=None, owner_type="character",
        owner=CHAR, installer=CHAR, completed=None, job_id=None):
    return IndustryJob(
        owner_type=owner_type, owner_id=owner, job_id=job_id or next(_job_ids), activity_id=activity,
        blueprint_type_id=blueprint, product_type_id=product, runs=runs, start_date=start,
        end_date=end, completed_date=completed, status=status, licensed_runs=licensed_runs,
        successful_runs=successful_runs, cost=cost, installer_id=installer,
        inputs=tuple(JobInput(t, q, s) for t, q, s in inputs), output_quantity=output_quantity,
    )


def tax_pair(tx: WalletTransaction, journal_id: int, tax: float):
    """market_transaction entry linked by context_id, tax entry at id+1."""
    gross = tx.unit_price * tx.quantity
    return [
        JournalEntry("character", tx.owner_id, journal_id, tx.date, "market_transaction", gross,
                     context_id=tx.transaction_id, context_id_type="market_transaction_id"),
        JournalEntry("character", tx.owner_id, journal_id + 1, tx.date, "transaction_tax", -tax),
    ]


def snapshot(**kw) -> LedgerSnapshot:
    kw.setdefault("t0", T0)
    kw.setdefault("tenant_character_ids", frozenset({CHAR, OTHER_CHAR}))
    kw.setdefault("home_structure_id", HOME)
    for name in ("production_type_ids", "tenant_character_ids", "relic_type_ids", "bpo_type_ids"):
        if name in kw:
            kw[name] = frozenset(kw[name])
    return LedgerSnapshot(**kw)


def remaining(result, type_id, unit=c.UNIT_ITEM):
    return [(l.origin, l.quantity_left, l.unit_cost)
            for l in result.layers if l.type_id == type_id and l.unit == unit]


def has_warning(result, prefix):
    return any(w.startswith(prefix) for w in result.warnings)


# ---------------------------------------------------------------- 1 full chain
MOON_GOO, FUEL = 100, 101
REACTION_BP, COMPOSITE = 200, 201
COMP_BP, T2_COMP = 210, 211
T1_BP, DATACORE = 300, 310
T2_BP, T2_MODULE = 320, 330


def full_chain_ledger(**overrides):
    goo = buy(MOON_GOO, 100, 10.0, at(1, 10), location=JITA, rate=100.0, m3=1.0)
    fuel = buy(FUEL, 50, 4.0, at(1, 10))
    cores = buy(DATACORE, 4, 50.0, at(1, 11))
    reaction = job(c.ACTIVITY_REACTION, REACTION_BP, COMPOSITE, 1, at(1, 12), at(2, 12), 300.0,
                   [(MOON_GOO, 100, "esi_blueprint"), (FUEL, 50, "esi_blueprint")], output_quantity=200)
    copy = job(c.ACTIVITY_COPYING, T1_BP, T1_BP, 1, at(1, 12), at(1, 14), 40.0, licensed_runs=2)
    invention = job(c.ACTIVITY_INVENTION, T1_BP, T2_BP, 2, at(2, 13), at(3, 13), 60.0,
                    [(DATACORE, 4, "probability_decryptor"), (T1_BP, 2, "probability_decryptor")],
                    licensed_runs=10, successful_runs=1)
    component = job(c.ACTIVITY_MANUFACTURING, COMP_BP, T2_COMP, 10, at(2, 14), at(3, 14), 100.0,
                    [(COMPOSITE, 100, "esi_blueprint")], output_quantity=10)
    module = job(c.ACTIVITY_MANUFACTURING, T2_BP, T2_MODULE, 5, at(3, 15), at(4, 15), 150.0,
                 [(T2_COMP, 10, "esi_blueprint")], output_quantity=5)
    sale = sell(T2_MODULE, 5, 400.0, at(5, 10))
    kw = dict(
        wallet_transactions=[goo, fuel, cores, sale],
        journal_entries=tax_pair(sale, 1000, 67.5),
        industry_jobs=[reaction, copy, invention, component, module],
        esi_stock_by_type={COMPOSITE: 100},
        production_type_ids={COMPOSITE, T2_COMP, T2_MODULE},
    )
    kw.update(overrides)
    return snapshot(**kw)


def test_full_chain_minerals_to_t2_module_sale():
    result = rebuild_costing(full_chain_ledger())

    # Reaction: goo 1000 + freight 100 (100 ISK/m3 x 1 m3) + fuel 200 + job 300
    # = 1600 for 200 composite -> 8.00/unit (bought 6.00, freight 0.50, job 1.50).
    # Copy: job 40 for 1 copy x 2 runs -> T1 BPC 20/run (job).
    # Invention: datacores 200 + 2 T1 runs 40 + job 60 = 300, all invention,
    # 1 success x 10 runs -> T2 BPC 30/run.
    # Component: 100 composite x 8 = 800 + job 100 = 900 / 10 -> 90/unit
    # (bought 60, freight 5, job 25).
    # Module: 10 components 900 + 5 T2 runs 150 + job 150 = 1200 / 5 -> 240/unit.
    # Sale: 5 x 400 = 2000 gross, real tax 67.50 -> revenue 1932.50, cost 1200.
    [r] = result.realized_results
    assert r.quantity == 5
    assert r.gross == pytest.approx(2000.0)
    assert r.sales_tax == pytest.approx(67.5)
    assert r.revenue == pytest.approx(1932.5)
    assert r.cost == pytest.approx(1200.0)
    assert r.profit == pytest.approx(732.5)
    assert r.components.materials_bought == pytest.approx(600.0)
    assert r.components.freight == pytest.approx(50.0)
    assert r.components.job == pytest.approx(400.0)
    assert r.components.invention == pytest.approx(150.0)
    assert r.components.materials_estimated == pytest.approx(0.0)
    assert r.coverage_share == pytest.approx(1.0)
    assert not r.estimated
    assert r.warnings == []

    # Leftovers: 100 composite at 8.00, 5 T2 BPC runs at 30. No opening
    # layers (ESI stock 100 composite == net flows).
    assert remaining(result, COMPOSITE) == [("produced", 100, pytest.approx(8.0))]
    assert remaining(result, T2_BP, c.UNIT_BPC_RUN) == [("produced", 5, pytest.approx(30.0))]
    assert [l for l in result.all_layers if l.origin == "opening"] == []
    assert len(result.layers) == 2
    states = {j.activity_id: j.state for j in result.jobs}
    assert set(states.values()) == {"produced"}
    assert result.work_in_progress == []
    assert result.warnings == []


def test_determinism_independent_of_input_order():
    ledger = full_chain_ledger()
    first = rebuild_costing(ledger)
    rng = random.Random(7)
    shuffled = dict(
        wallet_transactions=rng.sample(list(ledger.wallet_transactions), len(ledger.wallet_transactions)),
        industry_jobs=rng.sample(list(ledger.industry_jobs), len(ledger.industry_jobs)),
        journal_entries=list(reversed(ledger.journal_entries)),
    )
    second = rebuild_costing(full_chain_ledger(**shuffled))
    assert [(l.layer_id, l.type_id, l.quantity_left, l.unit_cost) for l in first.all_layers] == \
        [(l.layer_id, l.type_id, l.quantity_left, l.unit_cost) for l in second.all_layers]
    assert [(r.result_id, r.cost, r.profit) for r in first.realized_results] == \
        [(r.result_id, r.cost, r.profit) for r in second.realized_results]


# ---------------------------------------------------------------- 2 partial sales
MINERAL, PRODUCT, BP = 34, 700, 701


def test_partial_sales_span_layers_fifo():
    # Two builds: 5 units at (5 x 40 + 50)/5 = 50, then 5 at (5 x 50 + 50)/5 = 60.
    ledger = snapshot(
        wallet_transactions=[
            buy(MINERAL, 5, 40.0, at(1, 8)),
            buy(MINERAL, 5, 50.0, at(1, 9)),
            sell(PRODUCT, 3, 100.0, at(4)),
            sell(PRODUCT, 4, 100.0, at(5)),
        ],
        industry_jobs=[
            job(1, BP, PRODUCT, 5, at(1, 10), at(2), 50.0, [(MINERAL, 5, "esi_blueprint")], output_quantity=5),
            job(1, BP, PRODUCT, 5, at(2, 10), at(3), 50.0, [(MINERAL, 5, "esi_blueprint")], output_quantity=5),
        ],
        esi_stock_by_type={PRODUCT: 3},
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    first, second = result.realized_results
    # Sale 1: 3 x 50 = 150. Sale 2: 2 x 50 + 2 x 60 = 220.
    assert first.cost == pytest.approx(150.0)
    assert second.cost == pytest.approx(220.0)
    assert [d.quantity for d in second.draws] == [2, 2]
    # Tax missing -> revenue stays gross and the row is flagged, no modeled rate.
    assert first.revenue == pytest.approx(300.0) and "tax_missing" in first.warnings
    assert second.profit == pytest.approx(400.0 - 220.0)
    # 3 left of the second build at 60.
    assert remaining(result, PRODUCT) == [("produced", 3, pytest.approx(60.0))]


# ---------------------------------------------------------------- 3 job before T0
def test_job_before_t0_consumes_nothing_and_output_is_opening():
    before = job(1, BP, PRODUCT, 10, T0 - timedelta(days=1), at(1, 6), 999.0,
                 [(MINERAL, 100, "esi_blueprint")], output_quantity=10)
    ledger = snapshot(
        wallet_transactions=[sell(PRODUCT, 4, 100.0, at(2))],
        industry_jobs=[before],
        # Today: 6 products left after the 4 sold, 50 minerals untouched.
        esi_stock_by_type={PRODUCT: 6, MINERAL: 50},
        reference_prices={(PRODUCT, T0.date()): 70.0, (MINERAL, T0.date()): 5.0},
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    assert result.jobs == []
    assert result.jobs_before_t0 == [before.ref]
    # Opening product = 6 - (0 - 4) = 10 at 70; minerals = 50 - 0 = 50 at 5.
    openings = {o.type_id: (o.quantity, o.unit_cost, o.price_source) for o in result.opening_lines}
    assert openings == {MINERAL: (50, 5.0, "reference"), PRODUCT: (10, 70.0, "reference")}
    [r] = result.realized_results
    assert r.cost == pytest.approx(280.0)  # 4 x 70, all estimated
    assert r.components.materials_estimated == pytest.approx(280.0)
    assert r.coverage_share == 0.0 and r.estimated
    assert remaining(result, MINERAL) == [("opening", 50, pytest.approx(5.0))]
    assert remaining(result, PRODUCT) == [("opening", 6, pytest.approx(70.0))]


# ---------------------------------------------------------------- 4 negative opening
def test_negative_opening_is_clamped_with_warning():
    # Bought 10, ESI shows only 4 today (6 lost/fitted): opening = 4 - 10 = -6 -> 0.
    ledger = snapshot(
        wallet_transactions=[buy(MINERAL, 10, 5.0, at(1))],
        esi_stock_by_type={MINERAL: 4},
    )
    result = rebuild_costing(ledger)
    [line] = result.opening_lines
    assert line.raw_quantity == pytest.approx(-6.0)
    assert line.quantity == 0 and line.layer_id is None
    assert has_warning(result, "negative_opening: type 34")
    # No opening layer; the 10 bought stay (drift is the report's job).
    assert remaining(result, MINERAL) == [("bought", 10, pytest.approx(5.0))]


# ---------------------------------------------------------------- 5 unknown-ME BPC
def test_assumed_me_inputs_become_estimated_materials():
    ledger = snapshot(
        wallet_transactions=[buy(MINERAL, 100, 10.0, at(1, 8))],
        industry_jobs=[job(1, BP, PRODUCT, 10, at(1, 10), at(2), 250.0,
                           [(MINERAL, 100, "assumed_me")], output_quantity=10)],
        esi_stock_by_type={PRODUCT: 10},
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    [layer] = result.layers
    # 100 x 10 = 1000 bought, but the quantity is an assumption -> estimated.
    # Job cost 250 stays real. Per unit: estimated 100, job 25.
    assert layer.unit_components == CostComponents(materials_estimated=100.0, job=25.0)
    assert layer.assumed_me and not layer.assumed_decryptor
    assert layer.components.coverage() == pytest.approx(250.0 / 1250.0)
    assert has_warning(result, "assumed_me:")


# ---------------------------------------------------------------- 6 decryptor sources
def test_probability_vs_assumed_decryptor_invention():
    def invention(source, start):
        return job(c.ACTIVITY_INVENTION, T1_BP, T2_BP, 2, start, start + timedelta(hours=10), 60.0,
                   [(DATACORE, 4, source), (T1_BP, 2, source)], licensed_runs=10, successful_runs=1)

    proven = invention("probability_decryptor", at(2))
    assumed = invention("assumed_decryptor", at(3))
    ledger = snapshot(
        wallet_transactions=[buy(DATACORE, 8, 50.0, at(1, 8))],
        industry_jobs=[
            job(c.ACTIVITY_COPYING, T1_BP, T1_BP, 2, at(1, 9), at(1, 11), 40.0, licensed_runs=2),
            proven, assumed,
        ],
    )
    result = rebuild_costing(ledger)
    by_job = {l.job_id: l for l in result.layers if l.type_id == T2_BP}
    # Copy: 40 for 2 copies x 2 runs -> 10/run.
    # Proven: datacores 200 + T1 runs 20 + job 60 = 280 invention / 10 runs.
    assert by_job[proven.job_id].unit_components == CostComponents(invention=28.0)
    assert not by_job[proven.job_id].assumed_decryptor
    # Assumed: inputs 220 -> estimated, job 60 -> invention.
    assert by_job[assumed.job_id].unit_components == CostComponents(materials_estimated=22.0, invention=6.0)
    assert by_job[assumed.job_id].assumed_decryptor
    assert has_warning(result, f"assumed_decryptor: {assumed.ref}")


def test_invention_bpc_shortfall_draws_unknown_bpc_runs():
    # No copy job archived: the T1 BPC run is unknown origin, priced from the
    # BPC-run table (missing -> 0 + warning), never from the BPO item price.
    inv = job(c.ACTIVITY_INVENTION, T1_BP, T2_BP, 2, at(2), at(3), 60.0,
              [(DATACORE, 4, "probability_decryptor"), (T1_BP, 2, "probability_decryptor")],
              licensed_runs=10, successful_runs=2)
    ledger = snapshot(
        wallet_transactions=[buy(DATACORE, 4, 50.0, at(1))],
        industry_jobs=[inv],
        reference_prices={(T1_BP, at(2).date()): 1_000_000.0},
    )
    result = rebuild_costing(ledger)
    assert has_warning(result, "unknown_origin: type 300 (bpc_run)")
    assert has_warning(result, "missing_reference_price: type 300 (bpc_run)")
    [layer] = [l for l in result.layers if l.type_id == T2_BP]
    # 200 + 60 = 260 over 2 successes x 10 runs = 13/run.
    assert layer.quantity == 20 and layer.unit_cost == pytest.approx(13.0)


def test_failed_invention_is_its_own_loss_line():
    inv = job(c.ACTIVITY_INVENTION, T1_BP, T2_BP, 1, at(2), at(3), 30.0,
              [(DATACORE, 2, "probability_decryptor")], licensed_runs=10, successful_runs=0)
    result = rebuild_costing(snapshot(wallet_transactions=[buy(DATACORE, 2, 50.0, at(1))],
                                      industry_jobs=[inv]))
    [loss] = result.failed_invention_losses
    assert loss.cost == pytest.approx(130.0)
    assert [l for l in result.layers if l.type_id == T2_BP] == []


# ---------------------------------------------------------------- 7 cancelled job
def test_cancelled_job_is_a_loss_without_output():
    cancelled = job(1, BP, PRODUCT, 10, at(1, 10), at(3), 20.0, [(MINERAL, 10, "esi_blueprint")],
                    status="cancelled", completed=at(2), output_quantity=10)
    result = rebuild_costing(snapshot(
        wallet_transactions=[buy(MINERAL, 10, 5.0, at(1, 8))],
        industry_jobs=[cancelled],
        production_type_ids={PRODUCT},
    ))
    [loss] = result.cancelled_job_losses
    # Inputs 10 x 5 = 50 stay consumed, plus job cost 20 -> 70 lost on the cancel date.
    assert loss.cost == pytest.approx(70.0)
    assert loss.date == at(2)
    assert loss.components == CostComponents(materials_bought=50.0, job=20.0)
    assert result.layers == []
    assert result.jobs[0].state == "cancelled"


# ---------------------------------------------------------------- 8 D6 order
def test_sale_takes_produced_then_opening_then_bought():
    ledger = snapshot(
        wallet_transactions=[
            buy(MINERAL, 2, 25.0, at(1, 8)),
            buy(PRODUCT, 2, 100.0, at(1, 9)),
            sell(PRODUCT, 5, 200.0, at(3)),
        ],
        industry_jobs=[job(1, BP, PRODUCT, 2, at(1, 10), at(2), 50.0,
                           [(MINERAL, 2, "esi_blueprint")], output_quantity=2)],
        # Today 1 left: opening = 1 - (2 bought + 2 produced - 5 sold) = 2.
        esi_stock_by_type={PRODUCT: 1},
        reference_prices={(PRODUCT, T0.date()): 80.0},
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    [r] = result.realized_results
    # Produced 2 x (50 + 50)/2 = 50 -> 100, opening 2 x 80 = 160, bought 1 x 100.
    assert [(d.origin, d.quantity) for d in r.draws] == [("produced", 2), ("opening", 2), ("bought", 1)]
    assert r.cost == pytest.approx(360.0)
    assert remaining(result, PRODUCT) == [("bought", 1, pytest.approx(100.0))]
    assert has_warning(result, "trading_overlap: Production type 700")


def test_job_inputs_consume_plain_fifo_opening_first():
    ledger = snapshot(
        wallet_transactions=[buy(MINERAL, 10, 9.0, at(1, 8))],
        industry_jobs=[job(1, BP, PRODUCT, 1, at(1, 10), at(2), 0.0,
                           [(MINERAL, 15, "esi_blueprint")], output_quantity=1)],
        # Opening = 5 - (10 - 15) = 10 at 4.
        esi_stock_by_type={MINERAL: 5, PRODUCT: 1},
        reference_prices={(MINERAL, T0.date()): 4.0},
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    draws = result.jobs[0].draws
    assert [(d.origin, d.quantity) for d in draws] == [("opening", 10), ("bought", 5)]
    assert remaining(result, MINERAL) == [("bought", 5, pytest.approx(9.0))]


# ---------------------------------------------------------------- 9 D12 broker fees
def test_broker_fee_pool_spread_by_fill_value():
    OTHER, Q = 35, 702
    p_sale = sell(PRODUCT, 3, 100.0, at(2, 12), tx_id=7001)       # value 300, matches sell order
    m_fill = buy(OTHER, 20, 5.0, at(2, 13), tx_id=7002)           # value 100, matches buy order
    instant = buy(OTHER, 10, 6.0, at(2, 14), tx_id=7003)          # no own order at 6 -> instant buy
    q_fill = buy(Q, 10, 20.0, at(2, 15), tx_id=7004)              # value 200, matches buy order
    ledger = snapshot(
        wallet_transactions=[p_sale, m_fill, instant, q_fill],
        market_orders=[
            MarketOrder("character", CHAR, 1, PRODUCT, HOME, False, 100.0),
            MarketOrder("character", CHAR, 2, OTHER, HOME, True, 5.0),
            MarketOrder("character", CHAR, 3, Q, HOME, True, 20.0),
        ],
        journal_entries=[
            # Day 2 pool = 45 + 15 = 60.
            JournalEntry("character", CHAR, 1, at(2, 9), "brokers_fee", -45.0),
            JournalEntry("character", CHAR, 2, at(2, 9), "market_provider_tax", -15.0, context_id=HOME,
                         context_id_type="structure_id"),
            # Day 3: an order placed, nothing filled -> fees without sale.
            JournalEntry("character", CHAR, 3, at(3, 9), "brokers_fee", -15.0),
            # Before T0: ignored.
            JournalEntry("character", CHAR, 4, T0 - timedelta(hours=1), "brokers_fee", -99.0),
        ],
        # Opening product = 7 - (0 - 3) = 10 at 60.
        esi_stock_by_type={PRODUCT: 7, OTHER: 30, Q: 10},
        reference_prices={(PRODUCT, T0.date()): 60.0},
        production_type_ids={PRODUCT, Q},
    )
    result = rebuild_costing(ledger)
    # Shares of 60 over 600 ISK of order fills: P 30, OTHER 10, Q 20; instant 0.
    [r] = result.realized_results
    assert r.broker_fee == pytest.approx(30.0)
    assert r.cost == pytest.approx(3 * 60.0 + 30.0)
    assert r.profit == pytest.approx(300.0 - 210.0)
    assert result.fees_on_other_trading == pytest.approx(10.0)
    [q_layer] = [l for l in result.layers if l.type_id == Q]
    assert q_layer.unit_components == CostComponents(materials_bought=20.0, fees=2.0)
    [other_order, other_instant] = [l for l in result.layers if l.type_id == OTHER]
    assert other_order.unit_components.fees == 0 and other_instant.unit_components.fees == 0
    assert [(p.day, p.amount) for p in result.fees_without_sale] == [(at(3).date(), 15.0)]
    assert result.fees_without_sale_total == pytest.approx(15.0)
    # Every ISK of fees since T0 is accounted for once.
    assert r.broker_fee + result.fees_on_other_trading + 20.0 + result.fees_without_sale_total == \
        pytest.approx(75.0)


# ---------------------------------------------------------------- 10 freight
def test_freight_is_own_component_and_does_not_lower_coverage():
    ledger = snapshot(
        wallet_transactions=[
            buy(MINERAL, 10, 100.0, at(1, 8), location=JITA, rate=50.0, m3=2.0),
            sell(PRODUCT, 1, 2000.0, at(3)),
        ],
        industry_jobs=[job(1, BP, PRODUCT, 1, at(1, 10), at(2), 100.0,
                           [(MINERAL, 10, "esi_blueprint")], output_quantity=1)],
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    [r] = result.realized_results
    # 1000 bought + freight 50 x 2 m3 = 100 + job 100 = 1200.
    assert r.cost == pytest.approx(1200.0)
    assert r.components.freight == pytest.approx(100.0)
    assert r.components.materials_bought == pytest.approx(1000.0)
    assert r.coverage_share == pytest.approx(1.0)


# ---------------------------------------------------------------- 11 coverage threshold
def test_coverage_below_threshold_is_labeled_estimated():
    ledger = snapshot(
        wallet_transactions=[sell(PRODUCT, 1, 2000.0, at(3))],
        industry_jobs=[job(1, BP, PRODUCT, 1, at(1, 10), at(2), 100.0,
                           [(MINERAL, 100, "esi_blueprint")], output_quantity=1)],
        # Minerals came from before T0: opening = 0 - (0 - 100) = 100 at 9.
        reference_prices={(MINERAL, T0.date()): 9.0},
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    [r] = result.realized_results
    # 900 estimated + 100 job -> coverage 10 %.
    assert r.cost == pytest.approx(1000.0)
    assert r.coverage_share == pytest.approx(0.1)
    assert r.coverage_share < ESTIMATED_COVERAGE_THRESHOLD
    assert r.estimated and "estimated" in r.warnings
    assert has_warning(result, "coverage_below_threshold:")
    assert result.coverage() == pytest.approx(0.1)


def test_sale_before_purchase_draws_unknown_at_sale_date_price():
    # Sold 4 on day 1, bought 10 on day 2, 6 left: opening = 6 - (10 - 4) = 0.
    ledger = snapshot(
        wallet_transactions=[sell(PRODUCT, 4, 100.0, at(1)), buy(PRODUCT, 10, 50.0, at(2))],
        esi_stock_by_type={PRODUCT: 6},
        reference_prices={(PRODUCT, at(1).date()): 75.0},
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    [r] = result.realized_results
    assert [(d.origin, d.quantity) for d in r.draws] == [("unknown", 4)]
    assert r.cost == pytest.approx(300.0)
    assert r.coverage_share == 0.0 and "unknown_origin" in r.warnings
    assert remaining(result, PRODUCT) == [("bought", 10, pytest.approx(50.0))]


# ---------------------------------------------------------------- 12 D7 corp jobs
def test_corp_jobs_count_only_for_tenant_installers():
    mine = job(1, BP, PRODUCT, 1, at(1, 10), at(2), 10.0, [(MINERAL, 10, "esi_blueprint")],
               owner_type="corporation", owner=CORP, installer=CHAR, output_quantity=1)
    theirs = job(1, BP, PRODUCT, 1, at(1, 11), at(2), 10.0, [(MINERAL, 10, "esi_blueprint")],
                 owner_type="corporation", owner=CORP, installer=123456, output_quantity=1)
    ledger = snapshot(
        wallet_transactions=[buy(MINERAL, 20, 1.0, at(1, 8))],
        industry_jobs=[mine, theirs],
        # Corp hangar shows both products; the other member's minerals never left our layers.
        esi_stock_by_type={PRODUCT: 2, MINERAL: 10},
        reference_prices={(PRODUCT, T0.date()): 50.0},
        production_type_ids={PRODUCT},
    )
    result = rebuild_costing(ledger)
    assert [j.job_ref for j in result.jobs] == [mine.ref]
    assert result.skipped_corp_jobs == [theirs.ref]
    # Ours: 10 minerals + job 10 = 20. Theirs is invisible -> its product is opening.
    by_origin = {l.origin: (l.quantity_left, l.unit_cost) for l in result.layers if l.type_id == PRODUCT}
    assert by_origin == {"produced": (1, pytest.approx(20.0)), "opening": (1, pytest.approx(50.0))}
    assert remaining(result, MINERAL) == [("bought", 10, pytest.approx(1.0))]


# ---------------------------------------------------------------- misc rules
def test_research_jobs_go_to_capital_in_bpos():
    result = rebuild_costing(snapshot(industry_jobs=[
        job(c.ACTIVITY_RESEARCH_ME, BP, BP, 1, at(1), at(2), 500.0),
        job(c.ACTIVITY_RESEARCH_TE, BP, BP, 1, at(2), at(3), 250.0),
    ]))
    assert result.capital_in_bpos == {BP: 750.0}
    assert result.layers == []


def test_implausible_tax_falls_back_to_gross_with_flag():
    sale = sell(PRODUCT, 1, 100.0, at(2))
    result = rebuild_costing(snapshot(
        wallet_transactions=[sale],
        journal_entries=tax_pair(sale, 50, 40.0),  # 40 % - another sale's tax entry
        esi_stock_by_type={PRODUCT: 0},
        reference_prices={(PRODUCT, at(2).date()): 10.0},
        production_type_ids={PRODUCT},
    ))
    [r] = result.realized_results
    assert r.sales_tax is None and r.revenue == pytest.approx(100.0)
    assert "tax_missing" in r.warnings


def test_sale_outside_home_structure_is_warned():
    result = rebuild_costing(snapshot(
        wallet_transactions=[sell(PRODUCT, 1, 100.0, at(2), location=JITA)],
        esi_stock_by_type={PRODUCT: 0},
        reference_prices={(PRODUCT, at(2).date()): 10.0},
        production_type_ids={PRODUCT},
    ))
    assert "outside_home_structure" in result.realized_results[0].warnings
    assert has_warning(result, "outside_home_structure:")


def test_active_job_stays_work_in_progress():
    active = job(1, BP, PRODUCT, 1, at(1, 10), at(9), 10.0, [(MINERAL, 5, "esi_blueprint")], status="active")
    result = rebuild_costing(snapshot(wallet_transactions=[buy(MINERAL, 5, 2.0, at(1, 8))],
                                      industry_jobs=[active]))
    [wip] = result.work_in_progress
    assert wip.components.total == pytest.approx(20.0)
    assert result.layers == []

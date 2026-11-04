"""Reprocessing detection in the pure costing engine (realized/costing.py,
module docstring "Reprocessing"). Hand-built ledgers, no database, no ESI.

Fixture ore: one portion (100 units) of VELD yields 400 TRIT and 100 PYE
before yield; at 50% yield 1000 VELD -> 2000 TRIT + 500 PYE. COMP is its
compressed type: one unit = one portion (ratio 0.01)."""
from __future__ import annotations

from datetime import date

import pytest

from eve_trader.realized import costing as c
from eve_trader.realized.costing import (
    CompressionLink,
    JournalEntry,
    ReprocessRecipe,
    StockObservation,
    rebuild_costing,
)

from .test_production_realized_costing import CHAR, T0, at, buy, job, remaining, sell, snapshot

VELD = 1230
COMP = 62516
TRIT = 34
PYE = 35
MODULE = 3001
WIDGET = 7001
WIDGET_BP = 7002

RECIPES = {
    VELD: ReprocessRecipe(VELD, 100, ((TRIT, 400), (PYE, 100)), 0.5),
    COMP: ReprocessRecipe(COMP, 1, ((TRIT, 400), (PYE, 100)), 0.5),
}
LINK = CompressionLink(VELD, COMP, 0.01)


def prices(*days: int) -> dict:
    """TRIT 5 and PYE 20 ISK: 2000 TRIT and 500 PYE weigh the same."""
    out = {}
    for d in days:
        day = at(d).date()
        out[(TRIT, day)] = 5.0
        out[(PYE, day)] = 20.0
        out[(VELD, day)] = 12.0
        out[(COMP, day)] = 1200.0
    return out


def obs(day: int, hour: int = 12, **quantities) -> StockObservation:
    names = {"veld": VELD, "comp": COMP, "module": MODULE}
    return StockObservation(at(day, hour), {names[k]: float(v) for k, v in quantities.items()})


def ledger(*, txs=(), observations=(), jobs=(), journal=(), links=(), **kw):
    kw.setdefault("reference_prices", prices(*range(1, 12)))
    return snapshot(
        wallet_transactions=tuple(txs), stock_observations=tuple(observations),
        industry_jobs=tuple(jobs), journal_entries=tuple(journal),
        reprocess_recipes=kw.pop("recipes", RECIPES), compression_links=tuple(links), **kw,
    )


def cost_left(result, type_id) -> float:
    return sum(l.quantity_left * l.unit_cost for l in result.layers if l.type_id == type_id)


# ---------------------------------------------------------------- recipe rounding
def test_recipe_outputs_floor_whole_portions_then_each_material():
    r = ReprocessRecipe(VELD, 100, ((TRIT, 415), (PYE, 3)), 0.5)
    assert r.whole_quantity(250) == 200
    # 2 portions: TRIT floor(2*415*0.5)=415, PYE floor(2*3*0.5)=3
    assert r.outputs(250) == {TRIT: 415.0, PYE: 3.0}
    assert r.outputs(99) == {}


# ---------------------------------------------------------------- basic chain
def test_disappeared_ore_moves_its_cost_to_the_minerals():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=0)],
    ))
    assert remaining(result, VELD) == []
    [rec] = result.reprocess_records
    assert rec.kind == c.KIND_REPROCESS
    assert rec.quantity == 1000
    assert rec.outputs == {TRIT: 2000.0, PYE: 500.0}
    # Placed at the previous observation so a job in between can use it.
    assert rec.when == at(2)
    # 10,000 ISK split 50/50 by reference value (2000x5 vs 500x20).
    assert cost_left(result, TRIT) == pytest.approx(5000.0)
    assert cost_left(result, PYE) == pytest.approx(5000.0)
    assert cost_left(result, TRIT) + cost_left(result, PYE) == pytest.approx(10000.0)
    trit = [l for l in result.layers if l.type_id == TRIT][0]
    assert trit.origin == c.ORIGIN_REPROCESSED
    assert trit.unit_components.materials_bought == pytest.approx(2.5)
    assert not trit.estimated_origin
    # Listed in reprocess_records, not as a warning per event.
    assert not any(w.startswith("reprocess_") for w in result.warnings)


def test_without_observations_nothing_changes():
    result = rebuild_costing(ledger(txs=[buy(VELD, 1000, 10.0, at(1))]))
    assert remaining(result, VELD) == [(c.ORIGIN_BOUGHT, 1000, 10.0)]
    assert result.reprocess_records == []


def test_blocked_detection_warns_and_detects_nothing():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=0)],
        reprocess_detection_blocked="character 9001 shares wallet but not assets",
    ))
    assert result.reprocess_records == []
    assert any(w.startswith("reprocess_detection_off") for w in result.warnings)
    assert remaining(result, VELD) == [(c.ORIGIN_BOUGHT, 1000, 10.0)]


def test_job_between_observations_uses_the_reprocessed_minerals():
    build = job(c.ACTIVITY_MANUFACTURING, WIDGET_BP, WIDGET, 1, at(2, 18), at(2, 20), 100.0,
                inputs=[(TRIT, 2000, "computed")], output_quantity=1)
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        jobs=[build],
        observations=[obs(2, veld=1000), obs(3, veld=0)],
    ))
    assert not any(w.startswith("unknown_origin: type 34") for w in result.warnings)
    [rec] = result.jobs
    assert {d.origin for d in rec.draws} == {c.ORIGIN_REPROCESSED}
    # Widget = 5000 TRIT share + 100 job cost.
    assert cost_left(result, WIDGET) == pytest.approx(5100.0)


def test_sold_ore_is_not_reprocessed():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1)), sell(VELD, 400, 15.0, at(2, 18))],
        observations=[obs(2, veld=1000), obs(3, veld=600)],
    ))
    assert result.reprocess_records == []
    assert remaining(result, VELD) == [(c.ORIGIN_BOUGHT, 600, 10.0)]


def test_partial_portion_is_left_alone():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1050, 10.0, at(1))],
        observations=[obs(2, veld=1050), obs(3, veld=0)],
    ))
    [rec] = result.reprocess_records
    assert rec.quantity == 1000
    assert remaining(result, VELD) == [(c.ORIGIN_BOUGHT, 50, 10.0)]


def test_catch_up_on_first_observation_is_placed_at_the_last_purchase():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 600, 10.0, at(1)), buy(VELD, 400, 10.0, at(2))],
        observations=[obs(4, veld=0)],
    ))
    [rec] = result.reprocess_records
    assert rec.quantity == 1000
    assert rec.when == at(2)


# ---------------------------------------------------------------- false positives
def test_courier_in_transit_is_undone_when_the_ore_arrives():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=0), obs(5, veld=1000)],
    ))
    assert result.reprocess_records == []
    assert remaining(result, VELD) == [(c.ORIGIN_BOUGHT, 1000, 10.0)]


def test_transit_then_real_reprocessing_books_once():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=0), obs(5, veld=1000), obs(6, veld=0)],
    ))
    [rec] = result.reprocess_records
    assert rec.quantity == 1000
    assert rec.when == at(5)
    assert cost_left(result, TRIT) + cost_left(result, PYE) == pytest.approx(10000.0)


def test_move_seen_as_surplus_first_is_not_reprocessing():
    # Corp hangar synced first (ore counted twice), then the character.
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=2000), obs(3, 13, veld=1000)],
    ))
    assert result.reprocess_records == []
    assert remaining(result, VELD) == [(c.ORIGIN_BOUGHT, 1000, 10.0)]


def test_old_surplus_becomes_stock_without_a_layer():
    # 500 mined ore appear; three weeks later everything is reprocessed.
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1500), obs(25, veld=1500), obs(26, veld=0)],
        reference_prices=prices(*range(1, 30)),
    ))
    [rec] = result.reprocess_records
    assert rec.quantity == 1500
    # 1000 bought at 10 + 500 unknown at the 12 ISK reference price.
    origins = {d.origin for d in rec.draws}
    assert origins == {c.ORIGIN_BOUGHT, c.ORIGIN_UNKNOWN}
    assert rec.components.total == pytest.approx(10000.0 + 500 * 12.0)


# ---------------------------------------------------------------- compression
def test_compression_moves_cost_to_the_compressed_type():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=0, comp=10)],
        links=[LINK],
    ))
    [rec] = result.reprocess_records
    assert rec.kind == c.KIND_COMPRESS
    assert remaining(result, VELD) == []
    assert remaining(result, COMP) == [(c.ORIGIN_COMPRESSED, 10, pytest.approx(1000.0))]
    assert remaining(result, TRIT) == []


def test_compressed_ore_reprocessed_later_carries_the_raw_cost():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=0, comp=10), obs(4, comp=0)],
        links=[LINK],
    ))
    kinds = [r.kind for r in result.reprocess_records]
    assert kinds == [c.KIND_COMPRESS, c.KIND_REPROCESS]
    assert result.reprocess_records[1].outputs == {TRIT: 2000.0, PYE: 500.0}
    assert cost_left(result, TRIT) + cost_left(result, PYE) == pytest.approx(10000.0)


# ---------------------------------------------------------------- tax, opening, modules
def test_reprocessing_tax_in_the_window_is_a_fee_on_the_outputs():
    tax = JournalEntry("character", CHAR, 77, at(2, 20), c.REPROCESSING_TAX_REF_TYPE, -300.0)
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=0)],
        journal=[tax],
    ))
    [rec] = result.reprocess_records
    assert rec.tax == pytest.approx(300.0)
    assert cost_left(result, TRIT) + cost_left(result, PYE) == pytest.approx(10300.0)
    fees = sum(l.unit_components.fees * l.quantity_left for l in result.layers)
    assert fees == pytest.approx(300.0)


def test_tax_without_detected_reprocessing_is_warned():
    tax = JournalEntry("character", CHAR, 78, at(2, 20), c.REPROCESSING_TAX_REF_TYPE, -50.0)
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=1000)],
        journal=[tax],
    ))
    assert any(w.startswith("reprocessing_tax_unmatched") for w in result.warnings)


def test_mineral_opening_counts_reprocessed_output_and_ore_opening_is_not_warned():
    result = rebuild_costing(ledger(
        txs=[buy(VELD, 1000, 10.0, at(1))],
        observations=[obs(2, veld=1000), obs(3, veld=0)],
        esi_stock_by_type={TRIT: 2000.0, PYE: 500.0},
    ))
    # Today's minerals are the reprocessed ones: no opening layer for them.
    assert result.opening_raw[TRIT] == pytest.approx(0.0)
    assert [o for o, _q, _u in remaining(result, TRIT)] == [c.ORIGIN_REPROCESSED]
    assert not any(w.startswith("negative_opening: type 1230") for w in result.warnings)


def test_module_on_the_shortlist_is_reprocessed_at_its_own_yield():
    recipes = {MODULE: ReprocessRecipe(MODULE, 1, ((TRIT, 100),), 0.55)}
    result = rebuild_costing(ledger(
        txs=[buy(MODULE, 10, 1000.0, at(1))],
        observations=[obs(2, module=10), obs(3, module=0)],
        recipes=recipes,
    ))
    [rec] = result.reprocess_records
    assert rec.outputs == {TRIT: 550.0}
    assert cost_left(result, TRIT) == pytest.approx(10000.0)


def test_rebuild_is_deterministic():
    kw = dict(
        txs=[buy(VELD, 1000, 10.0, at(1), tx_id=1), buy(VELD, 500, 11.0, at(3), tx_id=2)],
        observations=[obs(2, veld=1000), obs(3, veld=0), obs(4, veld=500), obs(5, veld=0)],
    )
    a = rebuild_costing(ledger(**kw))
    b = rebuild_costing(ledger(**kw))
    assert [(r.ref, r.quantity, r.when) for r in a.reprocess_records] == \
           [(r.ref, r.quantity, r.when) for r in b.reprocess_records]
    assert len(a.reprocess_records) == 2
    assert isinstance(a.reprocess_records[0].when.date(), date)

import pytest

from eve_trader import storage
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.engine import _ReadinessLedger, _allocate_ready_runs


@pytest.fixture
def ledger(monkeypatch):
    on_hand: dict[int, float] = {}
    monkeypatch.setattr(engine, "_stock_on_hand", lambda type_id, *a, **k: on_hand.get(type_id, 0.0))
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}"))

    def make(stock: dict[int, float]) -> _ReadinessLedger:
        on_hand.clear()
        on_hand.update(stock)
        return _ReadinessLedger({}, ProductionConfig())
    return make


def _no_location(_type_id):
    return None


def _flat_tier(_type_id):
    return 0


def _allocate(runs, claims, coverage, led, tier=_flat_tier):
    requests = [(t, t, r, claims.get(t, [])) for t, r in runs.items()]
    return _allocate_ready_runs(requests, coverage, led, _no_location, tier=tier)


def test_everyone_ready_when_stock_is_abundant(ledger):
    ready, blockers = _allocate({1: 3, 2: 5}, {1: [(9, 30.0)], 2: [(9, 50.0)]}, {}, ledger({9: 1000.0}))
    assert ready == {1: 3, 2: 5}
    assert blockers == {1: [], 2: []}


def test_job_furthest_behind_goes_first_even_when_it_is_the_biggest(ledger):
    # The old smallest-claim-first rule gave the 100-unit claim nothing; now
    # the job with the lowest coverage takes what it can start first.
    ready, blockers = _allocate(
        {1: 10, 2: 1, 3: 4},
        {1: [(9, 100.0)], 2: [(9, 10.0)], 3: [(9, 40.0)]},
        {1: 0.02, 2: 0.5, 3: 0.16},
        ledger({9: 60.0}))
    assert ready == {1: 6, 3: 0, 2: 0}
    assert [(b.needed, b.covered) for b in blockers[1]] == [(100.0, 60.0)]
    assert [(b.needed, b.covered) for b in blockers[2]] == [(10.0, 0.0)]


def test_category_tier_beats_backlog(ledger):
    # Reaction (0) before Advanced Component (1) before Capital Component
    # (2) before an end product (3), even when the end product is further
    # behind.
    tiers = {1: 3, 2: 2, 3: 1, 4: 0}
    ready, _ = _allocate(
        {1: 10, 2: 10, 3: 10, 4: 10},
        {t: [(9, 10.0)] for t in (1, 2, 3, 4)},
        {1: 0.0, 2: 0.5, 3: 0.9, 4: 0.99},
        ledger({9: 25.0}), tier=tiers.get)
    assert ready == {4: 10, 3: 10, 2: 5, 1: 0}


def test_readiness_tier_maps_job_categories(monkeypatch):
    categories = {1: "Reactions", 2: "Advanced Components", 3: "Capital Components",
                  4: "Small Ships", 5: None}
    monkeypatch.setattr(engine, "job_category", categories.get)
    assert [engine._readiness_tier(t) for t in (1, 2, 3, 4, 5)] == [0, 1, 2, 3, 3]


def test_job_takes_only_what_its_scarcest_material_allows(ledger):
    # Job 1 is furthest behind but has only half its Rare (8); it must not
    # sit on all 100 Common, so job 2 still gets its 50.
    ready, blockers = _allocate(
        {1: 10, 2: 5},
        {1: [(9, 100.0), (8, 10.0)], 2: [(9, 50.0)]},
        {1: 0.0, 2: 0.5},
        ledger({9: 100.0, 8: 5.0}))
    assert ready == {1: 5, 2: 5}
    # Common was free for job 1, so only Rare is its blocker.
    assert [(b.type_id, b.needed, b.covered) for b in blockers[1]] == [(8, 10.0, 5.0)]
    assert blockers[2] == []


def test_unknown_coverage_counts_as_covered_and_ties_go_to_fewer_runs(ledger):
    ready, _ = _allocate(
        {1: 4, 2: 2, 3: 2},
        {1: [(9, 40.0)], 2: [(9, 20.0)], 3: [(9, 20.0)]},
        {1: 0.5, 2: None, 3: 0.5},
        ledger({9: 40.0}))
    # 3 (0.5, 2 runs) before 1 (0.5, 4 runs) before 2 (None).
    assert ready == {3: 2, 1: 2, 2: 0}


def test_zero_stock_and_jobs_without_materials(ledger):
    ready, blockers = _allocate({1: 5, 2: 3}, {1: [(9, 5.0)]}, {}, ledger({}))
    assert ready == {1: 0, 2: 3}
    assert [(b.needed, b.covered) for b in blockers[1]] == [(5.0, 0.0)]


def test_same_type_at_two_depths_is_two_requests(ledger):
    led = ledger({9: 15.0})
    ready, _ = _allocate_ready_runs(
        [((0, 1), 1, 1, [(9, 10.0)]), ((1, 1), 1, 1, [(9, 10.0)])], {}, led, _no_location,
        tier=_flat_tier)
    assert ready == {(0, 1): 1, (1, 1): 0}
    assert led.free_total(9) == 5.0

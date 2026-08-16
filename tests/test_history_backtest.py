from eve_trader.config import TradingConfig
from eve_trader.goonmetrics_client import HistoryPoint
from eve_trader.history_backtest import (
    _index_history, _score_candidate, find_new_import_candidates, select_candidate_window,
)
from eve_trader.models import Candidate


def _points(region_id, type_id, prices, movement=1.0):
    return [
        HistoryPoint(region_id=region_id, type_id=type_id, date=f"2026-01-{i+1:02d}",
                     min_price=p * 0.9, max_price=p * 1.1, avg_price=p,
                     movement=movement, num_orders=5)
        for i, p in enumerate(prices)
    ]


def test_consistently_profitable_candidate_is_recommended():
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95,
                         min_margin_threshold=0.05, min_hit_rate=0.3)
    candidate = Candidate(item="Profitable Thing", type_id=1, volume_m3=0.1,
                           category="Module/Rig", market_group_path="x")

    jita_prices = [1000, 1010, 990, 1005, 995]
    ref_prices = [1900, 1920, 1880, 1910, 1890]
    points = _points(cfg.jita_region_id, 1, jita_prices) + _points(cfg.reference_region_id, 1, ref_prices)
    idx = _index_history(points)

    result = _score_candidate(candidate, idx, cfg)
    assert result is not None
    assert result.paired_days == 5
    assert result.hit_rate == 1.0
    assert result.add is True
    assert result.score > 0


def test_unprofitable_candidate_is_not_recommended():
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95, min_margin_threshold=0.05)
    candidate = Candidate(item="Bad Thing", type_id=2, volume_m3=1.0,
                           category="Material", market_group_path="x")

    jita_prices = [1000, 1000, 1000]
    ref_prices = [1050, 1050, 1050]  # net_sell after haircut ~= 997.5 < landed (1000+900=1900)
    points = _points(cfg.jita_region_id, 2, jita_prices) + _points(cfg.reference_region_id, 2, ref_prices)
    idx = _index_history(points)

    result = _score_candidate(candidate, idx, cfg)
    assert result is not None
    assert result.add is False


def test_missing_history_returns_none():
    cfg = TradingConfig()
    candidate = Candidate(item="No History", type_id=3, volume_m3=1.0,
                           category="Material", market_group_path="x")
    result = _score_candidate(candidate, {}, cfg)
    assert result is None


def test_profitable_but_illiquid_candidate_is_not_recommended_when_min_avg_movement_set():
    # Confirmed with the user: profitability alone isn't enough - an item
    # also needs a minimum amount of real trading activity behind it.
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95,
                         min_margin_threshold=0.05, min_hit_rate=0.3, min_avg_movement=100.0)
    candidate = Candidate(item="Profitable But Dead", type_id=1, volume_m3=0.1,
                           category="Module/Rig", market_group_path="x")

    jita_prices = [1000, 1010, 990, 1005, 995]
    ref_prices = [1900, 1920, 1880, 1910, 1890]
    points = (_points(cfg.jita_region_id, 1, jita_prices)
              + _points(cfg.reference_region_id, 1, ref_prices, movement=5.0))  # below the 100.0 floor
    idx = _index_history(points)

    result = _score_candidate(candidate, idx, cfg)
    assert result is not None
    assert result.hit_rate == 1.0  # profitability itself is fine
    assert result.add is False     # but movement is too low to recommend it


def test_same_candidate_recommended_once_movement_clears_the_threshold():
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95,
                         min_margin_threshold=0.05, min_hit_rate=0.3, min_avg_movement=100.0)
    candidate = Candidate(item="Profitable And Liquid", type_id=1, volume_m3=0.1,
                           category="Module/Rig", market_group_path="x")

    jita_prices = [1000, 1010, 990, 1005, 995]
    ref_prices = [1900, 1920, 1880, 1910, 1890]
    points = (_points(cfg.jita_region_id, 1, jita_prices)
              + _points(cfg.reference_region_id, 1, ref_prices, movement=500.0))
    idx = _index_history(points)

    result = _score_candidate(candidate, idx, cfg)
    assert result is not None
    assert result.add is True


def _candidates(ids):
    return [Candidate(item=f"Item {i}", type_id=i, volume_m3=1.0, category="Material", market_group_path="x")
            for i in ids]


def test_select_candidate_window_covers_everything_within_bounded_runs():
    # Confirmed with the user: a fixed prefix leaves the tail unreachable
    # forever, and a random sample only gives every candidate a *chance* -
    # the rotating window must guarantee every candidate is eventually
    # tested, within a bounded number of runs.
    candidates = _candidates(range(1000))
    seen: set[int] = set()
    offset = 0
    runs = 0
    while len(seen) < len(candidates) and runs < 20:
        window, offset = select_candidate_window(candidates, max_ids=100, offset=offset)
        seen.update(c.type_id for c in window)
        runs += 1
    assert seen == {c.type_id for c in candidates}
    assert runs == 10  # ceil(1000 / 100)


def test_select_candidate_window_wraps_around_the_end():
    candidates = _candidates(range(5))
    window, next_offset = select_candidate_window(candidates, max_ids=3, offset=4)
    assert [c.type_id for c in window] == [4, 0, 1]
    assert next_offset == 2


def test_select_candidate_window_returns_everything_when_list_fits():
    candidates = _candidates(range(3))
    window, next_offset = select_candidate_window(candidates, max_ids=10, offset=0)
    assert [c.type_id for c in window] == [0, 1, 2]
    assert next_offset == 0


class _FakeGoonmetricsClient:
    """Same profitable price series as test_consistently_profitable_candidate_
    is_recommended above, applied to every type_id - lets tests focus on
    batching/fault-isolation rather than scoring math. `fail_type_ids`
    simulates a batch that can't be fetched at all (network outage, parse
    bug, etc.)."""

    def __init__(self, cfg, fail_type_ids=frozenset()):
        self.cfg = cfg
        self.fail_type_ids = fail_type_ids
        self.calls = 0

    def price_history_chunked(self, region_id, type_ids):
        self.calls += 1
        if any(t in self.fail_type_ids for t in type_ids):
            raise RuntimeError("simulated network failure")
        prices = [1000, 1010, 990, 1005, 995] if region_id == self.cfg.jita_region_id \
            else [1900, 1920, 1880, 1910, 1890]
        points = []
        for t in type_ids:
            points.extend(_points(region_id, t, prices))
        return points


def test_find_new_import_candidates_full_mode_processes_every_batch():
    # Confirmed with the user: "full search" must really check everything,
    # not just one 500-sized window - here batched into several small
    # batches (safe_mode_max_ids=3) to prove every batch gets processed and
    # saved incrementally via results_sink, not just the first one.
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95,
                         min_margin_threshold=0.05, min_hit_rate=0.3, safe_mode_max_ids=3)
    candidates = _candidates(range(10))
    client = _FakeGoonmetricsClient(cfg)
    saved_batches: list[list] = []

    results, next_offset = find_new_import_candidates(
        candidates, existing_item_ids=set(), client=client, cfg=cfg,
        results_sink=saved_batches.append)

    assert {r.type_id for r in results} == set(range(10))
    assert next_offset == 0  # full mode never advances the safe-mode cursor
    assert len(saved_batches) == 4  # 10 candidates / batch size 3 -> 3,3,3,1
    assert sum(len(b) for b in saved_batches) == 10


def test_find_new_import_candidates_full_mode_survives_one_bad_batch():
    # The whole point of "as robust as possible": one batch failing outright
    # (Goonmetrics down *and* the ESI fallback also failing, or an
    # unexpected bug) must not lose the results already computed for every
    # other batch.
    cfg = TradingConfig(import_cost_per_m3=900.0, structure_sell_haircut=0.95,
                         min_margin_threshold=0.05, min_hit_rate=0.3, safe_mode_max_ids=3)
    candidates = _candidates(range(10))
    client = _FakeGoonmetricsClient(cfg, fail_type_ids={3, 4, 5})
    saved_batches: list[list] = []

    results, _ = find_new_import_candidates(
        candidates, existing_item_ids=set(), client=client, cfg=cfg,
        results_sink=saved_batches.append)

    result_ids = {r.type_id for r in results}
    assert result_ids == set(range(10)) - {3, 4, 5}
    assert sum(len(b) for b in saved_batches) == len(result_ids)

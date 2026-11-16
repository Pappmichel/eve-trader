"""The market-velocity cache: the lock is not held over the network call."""
import threading

import pytest

from eve_trader.production import dynamic_targets, engine
from eve_trader.production.config import ProductionConfig


@pytest.fixture(autouse=True)
def _clean():
    engine.clear_w2_market_velocity_cache()
    yield
    engine.clear_w2_market_velocity_cache()


def test_a_blocked_key_does_not_block_another_key_and_both_stay_cached(monkeypatch):
    release_a = threading.Event()
    a_started = threading.Event()

    def fake(cfg, ids, **kw):
        if ids == [1]:
            a_started.set()
            assert release_a.wait(10)
        return {i: 1.0 for i in ids}

    monkeypatch.setattr(dynamic_targets, "compute_market_velocity", fake)
    cfg = ProductionConfig()
    result_a = {}
    t = threading.Thread(target=lambda: result_a.update(engine._w2_market_velocity(cfg, [1]) or {}))
    t.start()
    assert a_started.wait(10)
    # B runs to completion while A is still inside its network call.
    assert engine._w2_market_velocity(cfg, [2]) == {2: 1.0}
    release_a.set()
    t.join(10)
    assert result_a == {1: 1.0}
    assert len(engine._w2_market_cache) == 2
    assert engine._w2_market_velocity(cfg, [1]) == {1: 1.0}


def test_same_key_callers_share_one_computation(monkeypatch):
    calls = []
    release = threading.Event()
    started = threading.Event()

    def fake(cfg, ids, **kw):
        calls.append(ids)
        started.set()
        assert release.wait(10)
        return {i: 2.0 for i in ids}

    monkeypatch.setattr(dynamic_targets, "compute_market_velocity", fake)
    cfg = ProductionConfig()
    out = []
    threads = [threading.Thread(target=lambda: out.append(engine._w2_market_velocity(cfg, [7]))) for _ in range(3)]
    for th in threads:
        th.start()
    assert started.wait(10)
    release.set()
    for th in threads:
        th.join(10)
    assert out == [{7: 2.0}] * 3
    assert len(calls) == 1
    assert engine._w2_market_key_locks == {}


def test_outage_is_not_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(dynamic_targets, "compute_market_velocity", lambda cfg, ids, **k: calls.append(1) or {})
    cfg = ProductionConfig()
    assert engine._w2_market_velocity(cfg, [3]) is None
    assert engine._w2_market_velocity(cfg, [3]) is None
    assert len(calls) == 2
    assert engine._w2_market_cache == {}


def test_other_days_are_dropped_and_the_cache_is_capped(monkeypatch):
    monkeypatch.setattr(dynamic_targets, "compute_market_velocity", lambda cfg, ids, **k: {i: 1.0 for i in ids})
    cfg = ProductionConfig()
    for n in range(engine._W2_MARKET_CACHE_MAX + 4):
        engine._w2_market_velocity(cfg, [100 + n])
    assert len(engine._w2_market_cache) == engine._W2_MARKET_CACHE_MAX
    newest = max(k[4][0] for k in engine._w2_market_cache)
    assert newest == 100 + engine._W2_MARKET_CACHE_MAX + 3

    old_key = (1, "2000-01-01", 28, 0.1, (1,))
    engine._w2_market_cache[old_key] = {1: 1.0}
    engine._w2_market_velocity(cfg, [999])
    assert old_key not in engine._w2_market_cache

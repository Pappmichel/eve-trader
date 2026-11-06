"""Per-character contract-items pace and 520 ConStopSpamming handling."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from eve_trader.esi_client import ESIClient, ESIError, ESIRateLimited, ESIStatusError, MAX_RETRY_AFTER_WAIT_SECONDS


@pytest.fixture(autouse=True)
def _reset_limiter():
    ESIClient.clear_contract_items_limiter()
    yield
    ESIClient.clear_contract_items_limiter()


def test_sixteenth_call_within_window_waits(monkeypatch):
    clock = {"t": 1000.0}
    sleeps: list[float] = []
    monkeypatch.setattr(ESIClient, "_monotonic", staticmethod(lambda: clock["t"]))

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr("eve_trader.esi_client.time.sleep", fake_sleep)

    for _ in range(ESIClient._CONTRACT_ITEMS_MAX_PER_WINDOW):
        ESIClient._await_contract_items_slot(42)
    assert sleeps == []
    ESIClient._await_contract_items_slot(42)
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(ESIClient._CONTRACT_ITEMS_WINDOW_SECONDS)


def test_other_character_does_not_wait(monkeypatch):
    clock = {"t": 0.0}
    sleeps: list[float] = []
    monkeypatch.setattr(ESIClient, "_monotonic", staticmethod(lambda: clock["t"]))
    monkeypatch.setattr("eve_trader.esi_client.time.sleep", lambda s: sleeps.append(s))
    for _ in range(ESIClient._CONTRACT_ITEMS_MAX_PER_WINDOW):
        ESIClient._await_contract_items_slot(1)
    ESIClient._await_contract_items_slot(2)
    assert sleeps == []


def test_two_role_prefixes_share_one_character_budget(monkeypatch):
    clock = {"t": 0.0}
    sleeps: list[float] = []
    monkeypatch.setattr(ESIClient, "_monotonic", staticmethod(lambda: clock["t"]))

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr("eve_trader.esi_client.time.sleep", fake_sleep)
    for i in range(ESIClient._CONTRACT_ITEMS_MAX_PER_WINDOW):
        cid = ESIClient._character_id_from_role(
            "doctrine:99" if i % 2 == 0 else "producer:99")
        ESIClient._await_contract_items_slot(cid)
    assert sleeps == []
    ESIClient._await_contract_items_slot(99)
    assert sleeps


def test_threads_do_not_exceed_window(monkeypatch):
    clock = {"t": 0.0}
    lock = threading.Lock()
    monkeypatch.setattr(ESIClient, "_monotonic", staticmethod(lambda: clock["t"]))

    def fake_sleep(seconds):
        with lock:
            clock["t"] += seconds

    monkeypatch.setattr("eve_trader.esi_client.time.sleep", fake_sleep)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait(timeout=5)
            for _ in range(4):
                ESIClient._await_contract_items_slot(7)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    with ESIClient._contract_items_lock:
        # After all waits, at most MAX slots sit in the current window.
        now = clock["t"]
        live = [t for t in ESIClient._contract_items_times.get(7, [])
                if now - t < ESIClient._CONTRACT_ITEMS_WINDOW_SECONDS]
    assert len(live) <= ESIClient._CONTRACT_ITEMS_MAX_PER_WINDOW


def _resp(status, text):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.headers = {}
    r.json.return_value = {"error": text} if text.startswith("{") else {"error": text}
    return r


def _bare_client(monkeypatch):
    client = ESIClient.__new__(ESIClient)
    client.cfg = MagicMock(esi_base="https://esi.test")
    client.session = MagicMock()
    monkeypatch.setattr(client, "_await_error_budget", lambda: None)
    monkeypatch.setattr(ESIClient, "_record_error_budget", lambda *a, **k: None)
    return client


def _con_stop_resp(remaining_ticks: int):
    err = f'ConStopSpamming, details: {{"remainingTime": {remaining_ticks}}}'
    r = _resp(520, '{"error":"ConStopSpamming"}')
    r.text = f'{{"error":"{err}"}}'
    r.json.return_value = {"error": err}
    return r


def test_520_con_stop_spamming_retries_after_wait(monkeypatch):
    client = _bare_client(monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr("eve_trader.esi_client.time.sleep", lambda s: sleeps.append(s))
    ok = _resp(200, "[]")
    ok.json.return_value = []
    client.session.get.side_effect = [_con_stop_resp(int(2.0 * 1e7)), ok]
    out = client._get_response("/x/", retries=3)
    assert out is ok
    assert sleeps and sleeps[0] == pytest.approx(2.25)


def test_520_con_stop_spamming_too_long_raises_rate_limited(monkeypatch):
    client = _bare_client(monkeypatch)
    monkeypatch.setattr("eve_trader.esi_client.time.sleep", lambda s: None)
    client.session.get.return_value = _con_stop_resp(
        int((MAX_RETRY_AFTER_WAIT_SECONDS + 5) * 1e7))
    with pytest.raises(ESIRateLimited) as ei:
        client._get_response("/x/", retries=3)
    assert ei.value.group == "contract-items"


def test_520_without_con_stop_spamming_is_status_error(monkeypatch):
    client = _bare_client(monkeypatch)
    client.session.get.return_value = _resp(520, '{"error":"something else"}')
    with pytest.raises(ESIStatusError) as ei:
        client._get_response("/x/", retries=1)
    assert ei.value.status == 520


def test_520_unreadable_remaining_is_status_error(monkeypatch):
    client = _bare_client(monkeypatch)
    client.session.get.return_value = _resp(520, '{"error":"ConStopSpamming, details: broken"}')
    with pytest.raises(ESIStatusError):
        client._get_response("/x/", retries=1)

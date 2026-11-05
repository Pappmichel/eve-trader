"""ConfigProxy keeps monkeypatch working: a patched field is restored on undo."""
import contextvars

import pytest

from eve_trader.config import ConfigProxy, TradingConfig


def _proxy() -> tuple[ConfigProxy, TradingConfig]:
    instance = TradingConfig()
    return ConfigProxy(contextvars.ContextVar("cfg", default=instance)), instance


def test_monkeypatch_restores_a_field_on_undo():
    proxy, instance = _proxy()
    original = instance.scheduler_enabled

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(proxy, "scheduler_enabled", not original)
        assert proxy.scheduler_enabled is (not original)
        assert instance.scheduler_enabled is (not original)

    assert proxy.scheduler_enabled is original
    assert instance.scheduler_enabled is original


def test_proxy_dict_is_the_resolved_instance_dict():
    proxy, instance = _proxy()

    assert vars(proxy) is vars(instance)
    assert "_var" not in vars(proxy)

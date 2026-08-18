import pytest

from eve_trader import config
from eve_trader.production import config as production_config


@pytest.fixture(autouse=True)
def _reset_config_yaml_caches():
    # Module-level caches (see config.py/production/config.py's own
    # docstrings) - must be reset between tests or one test's tmp_path
    # config.yaml would leak into another's via the shared cache dict.
    config._trading_config_yaml_cache.clear()
    production_config._production_config_yaml_cache.clear()
    yield
    config._trading_config_yaml_cache.clear()
    production_config._production_config_yaml_cache.clear()


def test_load_trading_config_reads_disk_only_once_per_path(tmp_path, monkeypatch):
    # Confirmed real gap: resolve_and_set_trading_config calls this on every
    # gate-enabled request/scheduler tick - config.yaml can't change without
    # a process restart anyway, so a second call for the same path must not
    # touch disk again.
    path = tmp_path / "config.yaml"
    path.write_text("import_cost_per_m3: 123.0\n", encoding="utf-8")

    opens = []
    real_open = open

    def _counting_open(file, *args, **kwargs):
        opens.append(file)
        return real_open(file, *args, **kwargs)
    monkeypatch.setattr("builtins.open", _counting_open)

    first = config.load_trading_config(path)
    second = config.load_trading_config(path)

    assert first.import_cost_per_m3 == 123.0
    assert second.import_cost_per_m3 == 123.0
    assert len([o for o in opens if o == path]) == 1


def test_load_trading_config_returns_independent_copies(tmp_path):
    # Confirmed real gap: resolve_and_set_trading_config mutates its result
    # in place (apply_config_overrides, applying one tenant's own overrides)
    # - if load_trading_config ever returned the *same* cached instance, one
    # tenant's overrides would leak into every other tenant's config.
    path = tmp_path / "config.yaml"
    path.write_text("import_cost_per_m3: 123.0\n", encoding="utf-8")

    first = config.load_trading_config(path)
    first.import_cost_per_m3 = 999.0

    second = config.load_trading_config(path)
    assert second.import_cost_per_m3 == 123.0


def test_load_production_config_reads_disk_only_once_per_path(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("component_system_id: 30000142\n", encoding="utf-8")

    opens = []
    real_open = open

    def _counting_open(file, *args, **kwargs):
        opens.append(file)
        return real_open(file, *args, **kwargs)
    monkeypatch.setattr("builtins.open", _counting_open)

    first = production_config.load_production_config(path)
    second = production_config.load_production_config(path)

    assert first.component_system_id == 30000142
    assert second.component_system_id == 30000142
    assert len([o for o in opens if o == path]) == 1

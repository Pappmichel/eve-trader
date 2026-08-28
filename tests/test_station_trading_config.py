"""Tests for eve_trader/station_trading/config.py. Pure/no Postgres needed
(validate_config_overrides doesn't touch storage) - see test_tenant_scope.py
for resolve_and_set_station_trading_config (needs a real tenant_settings row).
"""
import pytest

from eve_trader.config import ConfigError, validate_config_overrides
from eve_trader.station_trading.config import StationTradingConfig


def test_validate_config_overrides_rejects_broker_fee_rate_above_1():
    cfg = StationTradingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"broker_fee_rate": 1.5})


def test_validate_config_overrides_rejects_negative_sales_tax_rate():
    cfg = StationTradingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"sales_tax_rate": -0.1})


def test_validate_config_overrides_rejects_min_spread_threshold_above_1():
    cfg = StationTradingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"min_spread_threshold": 1.5})


def test_validate_config_overrides_rejects_negative_min_daily_volume():
    cfg = StationTradingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"min_daily_volume": -1.0})


def test_validate_config_overrides_rejects_non_positive_station_id():
    cfg = StationTradingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"station_id": 0})


def test_validate_config_overrides_accepts_a_full_valid_settings_payload():
    cfg = StationTradingConfig()
    validate_config_overrides(cfg, {
        "station_id": 60003760, "broker_fee_rate": 0.03, "sales_tax_rate": 0.05,
        "min_spread_threshold": 0.08, "min_daily_volume": 1.0,
    })  # no raise

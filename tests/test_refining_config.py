"""Tests for eve_trader/refining/config.py - GitHub issue #90. Pure/no
Postgres needed (validate_refining_overrides/validate_config_overrides don't
touch storage) - see test_tenant_scope.py for resolve_and_set_refining_config
(needs a real tenant_settings row).
"""
import pytest

from eve_trader.config import ConfigError, validate_config_overrides
from eve_trader.refining.config import RefiningConfig, validate_refining_overrides


def test_validate_refining_overrides_rejects_unknown_structure_type():
    with pytest.raises(ConfigError, match="structure_type"):
        validate_refining_overrides({"structure_type": "Not A Real Structure"})


def test_validate_refining_overrides_rejects_unknown_rig_tier():
    with pytest.raises(ConfigError, match="rig_tier"):
        validate_refining_overrides({"rig_tier": "Not A Real Rig"})


def test_validate_refining_overrides_rejects_unknown_implant():
    with pytest.raises(ConfigError, match="implant"):
        validate_refining_overrides({"implant": "Not A Real Implant"})


def test_validate_refining_overrides_accepts_known_options():
    validate_refining_overrides({
        "structure_type": "Tatara (L Refinery)", "rig_tier": "T2-Rig", "implant": "RX-804",
    })  # no raise


def test_validate_config_overrides_rejects_skill_level_above_5():
    cfg = RefiningConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"reprocessing_skill_level": 15})


def test_validate_config_overrides_rejects_negative_skill_level():
    cfg = RefiningConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"scrapmetal_processing_skill_level": -1})


def test_validate_config_overrides_rejects_refining_tax_rate_above_1():
    cfg = RefiningConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"refining_tax_rate": 1.5})


def test_validate_config_overrides_rejects_security_status_out_of_range():
    cfg = RefiningConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"security_status": 2.0})


def test_validate_config_overrides_accepts_a_full_valid_settings_payload():
    cfg = RefiningConfig()
    validate_config_overrides(cfg, {
        "reprocessing_skill_level": 5, "reprocessing_efficiency_skill_level": 5,
        "scrapmetal_processing_skill_level": 5, "refining_tax_rate": 0.02, "security_status": -1.0,
    })  # no raise

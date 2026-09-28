"""Tests for eve_trader/module_reprocessing/config.py. Pure/no Postgres
needed (validate_config_overrides doesn't touch storage) - see
test_tenant_scope.py for resolve_and_set_module_reprocessing_config (needs a
real tenant_settings row).

ModuleReprocessingConfig has no enum-style fields (no structure_type/
rig_tier/implant - see that class's own docstring for why: the scrapmetal
path those would otherwise configure has no structure/rig/security/implant
effect at all), so unlike test_refining_config.py there is no
validate_module_reprocessing_overrides to test - only the generic
type/range check already wired into eve_trader.config._FIELD_RANGES.
"""
import pytest

from eve_trader.config import ConfigError, validate_config_overrides
from eve_trader.module_reprocessing.config import ModuleReprocessingConfig


def test_validate_config_overrides_rejects_skill_level_above_5():
    cfg = ModuleReprocessingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"scrapmetal_processing_skill_level": 15})


def test_validate_config_overrides_rejects_negative_skill_level():
    cfg = ModuleReprocessingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"scrapmetal_processing_skill_level": -1})


def test_validate_config_overrides_rejects_refining_tax_rate_above_1():
    cfg = ModuleReprocessingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"refining_tax_rate": 1.5})


def test_validate_config_overrides_rejects_negative_freight_cost():
    cfg = ModuleReprocessingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"freight_cost_per_m3": -1.0})


def test_validate_config_overrides_rejects_non_positive_purchase_region_id():
    cfg = ModuleReprocessingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"purchase_region_id": 0})


def test_validate_config_overrides_rejects_non_positive_max_active_shortlist_items():
    cfg = ModuleReprocessingConfig()
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, {"max_active_shortlist_items": 0})


def test_validate_config_overrides_accepts_a_full_valid_settings_payload():
    cfg = ModuleReprocessingConfig()
    validate_config_overrides(cfg, {
        "scrapmetal_processing_skill_level": 5, "refining_tax_rate": 0.02,
        "freight_cost_per_m3": 500.0, "min_profit_threshold": 100.0, "min_margin_threshold": 0.1,
        "purchase_region_id": 10000002, "enforce_shortlist_cap": True, "max_active_shortlist_items": 300,
    })  # no raise


def test_shortlist_cap_off_by_default():
    """Same "off by default, user opts in" shape as Station Trading's own
    enforce_shortlist_cap - the margin/profit threshold is the real filter."""
    cfg = ModuleReprocessingConfig()
    assert cfg.enforce_shortlist_cap is False
    assert cfg.max_active_shortlist_items == 300


def test_default_purchase_region_is_jita():
    """Pins the confirmed "Default Jita" requirement - a future change to a
    different default should show up as a deliberate diff to this test."""
    assert ModuleReprocessingConfig().purchase_region_id == 10000002


def test_purchase_structure_id_defaults_to_none_meaning_region_only():
    assert ModuleReprocessingConfig().purchase_structure_id is None


def test_no_structure_rig_fields_exist():
    """Confirms the deliberate scope decision (confirmed with the user):
    structure/rig/security/implant have zero effect on module/drone
    (scrapmetal) reprocessing, so they must not exist here as settings that
    would misleadingly suggest otherwise."""
    fields = {f.name for f in __import__("dataclasses").fields(ModuleReprocessingConfig)}
    assert not fields & {"structure_type", "rig_tier", "security_status", "implant"}

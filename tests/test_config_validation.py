import pytest

from eve_trader.actions import ActionError
from eve_trader.config import ConfigError, TradingConfig, apply_config_overrides, validate_config_overrides
from eve_trader.production import actions as production_actions
from eve_trader.production.config import ProductionConfig, validate_production_overrides


def test_validate_accepts_correctly_typed_overrides():
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"jita_region_id": 10000002, "import_cost_per_m3": 900.0, "buyer_character_name": "X"})
    # No exception - nothing else to assert.


def test_validate_accepts_int_for_float_field():
    # YAML's `900` parses as a Python int, not a float - a legitimate whole
    # number must still be accepted for a float-typed field like import_cost_per_m3.
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"import_cost_per_m3": 900})


def test_validate_rejects_string_for_float_field():
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="import_cost_per_m3"):
        validate_config_overrides(cfg, {"import_cost_per_m3": "900"})


def test_validate_rejects_string_for_int_field():
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="jita_region_id"):
        validate_config_overrides(cfg, {"jita_region_id": "10000002"})


def test_validate_rejects_bool_for_int_field():
    # bool is technically an int subclass in Python - must not be silently accepted.
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="jita_region_id"):
        validate_config_overrides(cfg, {"jita_region_id": True})


def test_validate_rejects_number_for_string_field():
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="buyer_character_name"):
        validate_config_overrides(cfg, {"buyer_character_name": 12345})


def test_validate_accepts_none_for_optional_field():
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"buyer_character_id": None})


def test_validate_rejects_wrong_type_for_optional_field():
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="buyer_character_id"):
        validate_config_overrides(cfg, {"buyer_character_id": "not a number"})


def test_validate_accepts_bool_field():
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"enforce_shortlist_cap": True})


def test_validate_rejects_string_for_bool_field():
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="enforce_shortlist_cap"):
        validate_config_overrides(cfg, {"enforce_shortlist_cap": "true"})


def test_validate_accepts_list_for_tuple_field():
    # YAML sequences always deserialize as list, never tuple.
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"excluded_path_prefixes": ["ships", "blueprints"]})


def test_validate_ignores_unknown_keys():
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"this_field_does_not_exist": 123})


def test_validate_does_not_apply_anything():
    cfg = TradingConfig()
    original = cfg.jita_region_id
    validate_config_overrides(cfg, {"jita_region_id": 999})
    assert cfg.jita_region_id == original  # validate-only, apply is a separate step


def test_apply_sets_values_after_validation():
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"jita_region_id": 999})
    apply_config_overrides(cfg, {"jita_region_id": 999})
    assert cfg.jita_region_id == 999


def test_production_config_type_validation():
    cfg = ProductionConfig()
    with pytest.raises(ConfigError, match="min_margin"):
        validate_config_overrides(cfg, {"min_margin": "fifteen percent"})


def test_validate_rejects_negative_id_field():
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="jita_region_id"):
        validate_config_overrides(cfg, {"jita_region_id": -1})


def test_validate_rejects_fee_fraction_above_one():
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="jita_buy_broker_fee"):
        validate_config_overrides(cfg, {"jita_buy_broker_fee": 1.5})


def test_validate_rejects_negative_fee_fraction():
    cfg = TradingConfig()
    with pytest.raises(ConfigError, match="jita_buy_broker_fee"):
        validate_config_overrides(cfg, {"jita_buy_broker_fee": -0.01})


def test_validate_accepts_fee_fraction_at_boundary():
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"jita_buy_broker_fee": 1.0})
    validate_config_overrides(cfg, {"jita_buy_broker_fee": 0.0})


def test_validate_allows_margin_field_above_one():
    # Margin can legitimately exceed 100% (see production/engine.py's
    # MAX_PLAUSIBLE_BUILD_MARGIN, capped at 300% - not 1.0) - a config asking
    # for at least 150% margin is a real, intentional value, not a typo.
    cfg = ProductionConfig()
    validate_config_overrides(cfg, {"min_margin": 1.5})


def test_validate_rejects_skill_level_above_five():
    # EVE's own hard skill-level cap - 0-5, never higher.
    cfg = ProductionConfig()
    with pytest.raises(ConfigError, match="encryption_skill_level"):
        validate_config_overrides(cfg, {"encryption_skill_level": 6})


def test_validate_ignores_range_for_fields_with_no_declared_bounds():
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"buyer_character_name": "anything at all"})


def test_validate_allows_none_for_optional_ranged_field():
    cfg = TradingConfig()
    validate_config_overrides(cfg, {"buyer_character_id": None})


def test_do_update_settings_rejects_out_of_range_value_as_action_error():
    from eve_trader import actions

    cfg = TradingConfig()
    with pytest.raises(ActionError, match="jita_buy_broker_fee"):
        actions.do_update_settings({"jita_buy_broker_fee": 5.0}, cfg=cfg)


def test_validate_production_overrides_accepts_known_structure_type():
    validate_production_overrides({"reaction_structure_type": "Athanor (M Refinery)"})


def test_validate_production_overrides_rejects_unknown_structure_type():
    with pytest.raises(ConfigError, match="reaction_structure_type"):
        validate_production_overrides({"reaction_structure_type": "Not A Real Structure"})


def test_validate_production_overrides_rejects_unknown_rig_tier():
    with pytest.raises(ConfigError, match="component_rig_tier"):
        validate_production_overrides({"component_rig_tier": "T3-Rig"})


def test_do_update_settings_rejects_bad_type_as_action_error(monkeypatch):
    from eve_trader import actions

    cfg = TradingConfig()
    with pytest.raises(ActionError, match="import_cost_per_m3"):
        actions.do_update_settings({"import_cost_per_m3": "not a number"}, cfg=cfg)


def test_production_do_update_settings_rejects_bad_structure_type():
    cfg = ProductionConfig()
    with pytest.raises(ActionError, match="reaction_structure_type"):
        production_actions.do_update_settings({"reaction_structure_type": "Bogus Structure"}, cfg=cfg)

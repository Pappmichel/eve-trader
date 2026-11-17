"""Config traps: shared YAML keys, market slug and range checks, unknown stored
override keys and `eve-trader admin prune-settings`."""
import contextvars
import logging
import uuid

import pytest
from click.testing import CliRunner

from eve_trader import admin, cli, config, goonmetrics_client, storage
from eve_trader.config import ConfigError, TradingConfig, validate_config_overrides
from eve_trader.config_scopes import tenant_config_classes
from eve_trader.module_reprocessing.config import ModuleReprocessingConfig
from eve_trader.pi.config import PiConfig
from eve_trader.production.config import ProductionConfig

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_warning_state():
    config._warned_collision_paths.clear()
    config._warned_unknown_keys.clear()
    yield
    config._warned_collision_paths.clear()
    config._warned_unknown_keys.clear()


# -- shared YAML keys -------------------------------------------------------


def test_shared_yaml_key_warns_once_and_names_the_classes(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text("hub_region_id: 10000043\nstructure_id: 5\n", encoding="utf-8")
    classes = tuple(tenant_config_classes().values())
    with caplog.at_level(logging.WARNING, logger="eve_trader.config"):
        found = config.warn_yaml_key_collisions(classes, path)
    warnings = [r for r in caplog.records if "hub_region_id" in r.getMessage()]
    assert len(warnings) == 1
    assert set(found) == {"hub_region_id"}
    for name in ("ProductionConfig", "DoctrineConfig", "PiConfig", "RefiningConfig", "StationTradingConfig"):
        assert name in warnings[0].getMessage()
    assert not [r for r in caplog.records if "structure_id" in r.getMessage()]


def test_unique_yaml_key_does_not_warn(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text("structure_id: 5\nhaul_cost_per_m3: 100\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="eve_trader.config"):
        assert config.warn_yaml_key_collisions(tuple(tenant_config_classes().values()), path) == {}
    assert not caplog.records


def test_second_load_of_the_same_file_does_not_warn_again(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text("hub_region_id: 10000043\n", encoding="utf-8")
    classes = tuple(tenant_config_classes().values())
    with caplog.at_level(logging.WARNING, logger="eve_trader.config"):
        config.warn_yaml_key_collisions(classes, path)
        assert len(caplog.records) == 1
        assert config.warn_yaml_key_collisions(classes, path) == {}
    assert len(caplog.records) == 1


def test_allowlisted_key_is_not_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "YAML_SHARED_KEY_ALLOWLIST", frozenset({"hub_region_id"}))
    path = tmp_path / "config.yaml"
    path.write_text("hub_region_id: 10000043\n", encoding="utf-8")
    assert config.warn_yaml_key_collisions(tuple(tenant_config_classes().values()), path) == {}


# -- slugs and ranges ---------------------------------------------------------

_SLUG_CLASSES = {
    "structure_market_slug": TradingConfig,
    "home_market": ProductionConfig,
    "pi_price_structure_slug": PiConfig,
}


@pytest.mark.parametrize("field", sorted(_SLUG_CLASSES))
@pytest.mark.parametrize("slug", ["C-J6MT", "my_market", "jita", "a", None, ""])
def test_valid_slugs_are_accepted(field, slug):
    if slug is None and field == "pi_price_structure_slug":
        pytest.skip("a plain str field: unset is the empty string")
    validate_config_overrides(_SLUG_CLASSES[field](), {field: slug})


@pytest.mark.parametrize("field", sorted(_SLUG_CLASSES))
@pytest.mark.parametrize("slug", ["../x", "a/b", "a?b", "a b", "-x", "x" * 65, "a#b", "a%2Fb"])
def test_invalid_slugs_are_rejected_with_the_field_name(field, slug):
    with pytest.raises(ConfigError, match=field):
        validate_config_overrides(_SLUG_CLASSES[field](), {field: slug})


def test_slug_of_64_characters_is_accepted():
    validate_config_overrides(TradingConfig(), {"structure_market_slug": "x" * 64})


def test_stored_invalid_slug_is_ignored_on_apply(caplog):
    cfg = ProductionConfig()
    cfg.home_market = "Good"
    with caplog.at_level(logging.WARNING, logger="eve_trader.config"):
        config.apply_config_overrides(cfg, {"home_market": "../etc", "haul_cost_per_m3": 123.0})
    assert cfg.home_market == "Good"
    assert cfg.haul_cost_per_m3 == 123.0
    assert any("home_market" in r.getMessage() and "../etc" not in r.getMessage() for r in caplog.records)


def test_goonmetrics_url_quotes_the_market(monkeypatch):
    seen = []

    class _Resp:
        text = "[]"
        content = b"[]"

        def raise_for_status(self):
            pass

        def json(self):
            return []

    client = goonmetrics_client.GoonmetricsClient()

    def _get(url, **kwargs):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(client.session, "get", _get)
    monkeypatch.setattr(goonmetrics_client, "_prices_cache", {})
    try:
        client.current_prices("a b/c")
    except Exception:  # noqa: BLE001 - only the requested URL matters here
        pass
    assert seen and "/market/a%20b%2Fc/prices.json" in seen[0]


def test_pi_min_isk_threshold_range():
    cfg = PiConfig()
    validate_config_overrides(cfg, {"pi_min_isk_per_planet_day": 0})
    with pytest.raises(ConfigError, match="pi_min_isk_per_planet_day"):
        validate_config_overrides(cfg, {"pi_min_isk_per_planet_day": -1})


def test_removed_dead_fields_are_gone():
    assert not hasattr(TradingConfig(), "buyer_character_id")
    assert not hasattr(TradingConfig(), "seller_character_id")
    assert not hasattr(ModuleReprocessingConfig(), "purchase_structure_id")


# -- unknown stored keys ------------------------------------------------------


def test_unknown_override_keys_lists_only_non_fields():
    unknown = config.unknown_override_keys(ProductionConfig, {"haul_cost_per_m3": 1, "jita_buy_broker_fee": 0.01})
    assert unknown == ["jita_buy_broker_fee"]


def test_unknown_key_warning_is_once_per_scope_and_key_without_values(caplog):
    with caplog.at_level(logging.WARNING, logger="eve_trader.config"):
        config.warn_unknown_override_keys("production", ProductionConfig, {"gone_field": "secret-value"})
        config.warn_unknown_override_keys("production", ProductionConfig, {"gone_field": "secret-value"})
        config.warn_unknown_override_keys("trading", TradingConfig, {"gone_field": 1})
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert all("gone_field" in m and "secret-value" not in m for m in messages)


@pytest.fixture
def two_tenants():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    with storage.connect_unscoped() as conn:
        for t in (a, b):
            conn.execute("INSERT INTO tenants (tenant_id, name) VALUES (?, ?)", (t, f"prune-{t[:8]}"))
    yield a, b
    pg_helpers.clean_tables([a, b], "tenant_settings")
    with storage.connect_unscoped() as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id IN (?, ?)", (a, b))


def _stored(tenant_id, scope):
    with storage.tenant_context(tenant_id):
        return storage.load_tenant_settings(scope)


def _seed(tenant_id, scope, values):
    with storage.tenant_context(tenant_id):
        storage.save_tenant_settings(scope, values)


@pg_helpers.postgres_required()
def test_prune_dry_run_changes_nothing_and_apply_removes_only_unknown_keys(two_tenants):
    a, b = two_tenants
    _seed(a, "production", {"haul_cost_per_m3": 321.0, "jita_buy_broker_fee": 0.01})
    _seed(a, "trading", {"import_cost_per_m3": 5.0, "buyer_character_id": 42})
    _seed(b, "production", {"haul_cost_per_m3": 111.0})

    dry = admin.do_prune_settings()
    mine = {(r["tenant_id"], r["scope"]): r["keys"] for r in dry["changed"] if r["tenant_id"] in (a, b)}
    assert mine == {(a, "production"): ["jita_buy_broker_fee"], (a, "trading"): ["buyer_character_id"]}
    assert dry["applied"] is False
    assert _stored(a, "production") == {"haul_cost_per_m3": 321.0, "jita_buy_broker_fee": 0.01}

    done = admin.do_prune_settings(apply=True)
    assert done["applied"] is True
    assert _stored(a, "production") == {"haul_cost_per_m3": 321.0}
    assert _stored(a, "trading") == {"import_cost_per_m3": 5.0}
    assert _stored(b, "production") == {"haul_cost_per_m3": 111.0}

    again = admin.do_prune_settings(apply=True)
    assert not [r for r in again["changed"] if r["tenant_id"] in (a, b)]


def _invoke(runner, args):
    """cli.main sets the Default tenant for the whole process (it is the CLI's
    one-time setup); run it in a copied context so that ambient tenant and the
    resolved configs do not leak into later tests on this worker."""
    return contextvars.copy_context().run(runner.invoke, cli.main, args)


@pg_helpers.postgres_required()
def test_prune_cli_is_dry_run_by_default(two_tenants):
    a, _ = two_tenants
    _seed(a, "pi", {"hub_region_id": 10000002, "obsolete_pi_key": 1})
    runner = CliRunner()
    result = _invoke(runner, ["admin", "prune-settings"])
    assert result.exit_code == 0, result.output
    assert "would remove pi: obsolete_pi_key" in result.output
    assert "(dry run)" in result.output
    assert "obsolete_pi_key" in _stored(a, "pi")
    result = _invoke(runner, ["admin", "prune-settings", "--apply"])
    assert result.exit_code == 0, result.output
    assert _stored(a, "pi") == {"hub_region_id": 10000002}

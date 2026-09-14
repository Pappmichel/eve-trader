"""P5-05: legacy access_gate_enabled: false cannot silently survive."""
from __future__ import annotations

from pathlib import Path

from eve_trader.config import (
    ACCESS_GATE_OFF_ENV,
    AccessConfig,
    apply_access_gate_policy,
    env_allows_gate_off,
    load_access_config,
)

_ROOT = Path(__file__).resolve().parent.parent


def test_fresh_dataclass_defaults_to_gate_enabled():
    assert AccessConfig().access_gate_enabled is True


def test_missing_config_file_defaults_to_gate_enabled(tmp_path):
    cfg = load_access_config(tmp_path / "no-such.yaml")
    assert cfg.access_gate_enabled is True


def test_legacy_false_is_forced_on_without_opt_out(tmp_path, monkeypatch):
    monkeypatch.delenv(ACCESS_GATE_OFF_ENV, raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("access_gate_enabled: false\n", encoding="utf-8")
    cfg = load_access_config(path)
    assert cfg.access_gate_enabled is True


def test_explicit_dev_opt_out_keeps_gate_off(tmp_path, monkeypatch):
    monkeypatch.setenv(ACCESS_GATE_OFF_ENV, "1")
    path = tmp_path / "config.yaml"
    path.write_text("access_gate_enabled: false\n", encoding="utf-8")
    cfg = load_access_config(path)
    assert cfg.access_gate_enabled is False


def test_yaml_true_stays_true_even_with_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv(ACCESS_GATE_OFF_ENV, "1")
    path = tmp_path / "config.yaml"
    path.write_text("access_gate_enabled: true\n", encoding="utf-8")
    cfg = load_access_config(path)
    assert cfg.access_gate_enabled is True


def test_policy_precedence_env_values():
    cfg = AccessConfig(access_gate_enabled=False)
    assert apply_access_gate_policy(cfg, environ={}).access_gate_enabled is True
    cfg = AccessConfig(access_gate_enabled=False)
    assert apply_access_gate_policy(cfg, environ={ACCESS_GATE_OFF_ENV: "1"}).access_gate_enabled is False
    cfg = AccessConfig(access_gate_enabled=False)
    assert apply_access_gate_policy(cfg, environ={ACCESS_GATE_OFF_ENV: "true"}).access_gate_enabled is False
    cfg = AccessConfig(access_gate_enabled=False)
    assert apply_access_gate_policy(cfg, environ={ACCESS_GATE_OFF_ENV: "0"}).access_gate_enabled is True


def test_example_config_enables_the_gate():
    import re
    text = (_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^access_gate_enabled:\s*true\s*$", text)
    assert re.search(r"(?m)^access_gate_enabled:\s*false\s*$", text) is None


def test_deploy_and_setup_rewrite_legacy_false():
    setup = (_ROOT / "deploy" / "setup.sh").read_text(encoding="utf-8")
    deploy = (_ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    for text in (setup, deploy):
        assert "access_gate_enabled: false" in text
        assert "access_gate_enabled: true" in text
        assert "EVE_TRADER_ALLOW_GATE_OFF" in text


def test_env_allows_gate_off_helper():
    assert env_allows_gate_off({ACCESS_GATE_OFF_ENV: "yes"}) is True
    assert env_allows_gate_off({ACCESS_GATE_OFF_ENV: "no"}) is False
    assert env_allows_gate_off({}) is False

"""Phase 4 of docs/REACTION_CAPACITY_PLAN.md: chaining-choice storage, settings
validation, stale-plan flag, and the thin API routes around them."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.config import ConfigError, apply_config_overrides, validate_config_overrides
from eve_trader.production import actions as production_actions
from eve_trader.production.config import ProductionConfig, validate_production_overrides

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401
from .test_storage_production_buy_list import _apply_production_buy_list_schema  # noqa: F401

client = TestClient(create_app())


# ------------------------------------------------------------------- validation

def test_reaction_window_next_rejects_non_iso():
    with pytest.raises(ConfigError, match="reaction_window_next"):
        validate_production_overrides({"reaction_window_next": "not-a-date"})


def test_reaction_window_next_accepts_iso_and_null():
    validate_production_overrides({"reaction_window_next": None})
    validate_production_overrides({"reaction_window_next": "2026-11-03T18:00:00+00:00"})
    validate_production_overrides({"reaction_window_next": "2026-11-03T18:00:00Z"})


def test_reaction_capacity_fields_are_range_checked():
    cfg = ProductionConfig()
    validate_config_overrides(cfg, {"reaction_cycle_days": 13})
    validate_config_overrides(cfg, {"reaction_slot_override": None})
    validate_config_overrides(cfg, {"reaction_slot_override": 0})
    with pytest.raises(ConfigError, match="reaction_cycle_days"):
        validate_config_overrides(cfg, {"reaction_cycle_days": 0})
    with pytest.raises(ConfigError, match="reaction_cycle_days"):
        validate_config_overrides(cfg, {"reaction_cycle_days": 31})
    with pytest.raises(ConfigError, match="reaction_formula_payback_cycles"):
        validate_config_overrides(cfg, {"reaction_formula_payback_cycles": 0})
    with pytest.raises(ConfigError, match="reaction_next_window_demand_factor"):
        validate_config_overrides(cfg, {"reaction_next_window_demand_factor": -1})


def test_do_update_settings_rejects_bad_window_as_action_error():
    cfg = ProductionConfig()
    with pytest.raises(ActionError, match="reaction_window_next"):
        production_actions.do_update_settings(
            {"reaction_window_next": "tomorrow"}, cfg=cfg,
        )


# ------------------------------------------------------------------- actions / stale

def test_do_set_reaction_chain_choice_validates_and_marks_stale(monkeypatch):
    calls = {"upsert": None, "delete": 0, "stale": 0}
    monkeypatch.setattr(
        storage, "upsert_reaction_chain_choice",
        lambda type_id, choice: calls.__setitem__("upsert", (type_id, choice)),
    )
    monkeypatch.setattr(
        storage, "delete_reaction_chain_choice",
        lambda type_id: calls.__setitem__("delete", calls["delete"] + 1),
    )
    monkeypatch.setattr(
        production_actions, "_mark_cached_production_plans_stale",
        lambda: calls.__setitem__("stale", calls["stale"] + 1),
    )

    assert production_actions.do_set_reaction_chain_choice(16670, "wait") == {
        "type_id": 16670, "choice": "wait",
    }
    assert calls["upsert"] == (16670, "wait")
    assert calls["stale"] == 1

    assert production_actions.do_set_reaction_chain_choice(16670, None) == {
        "type_id": 16670, "choice": None,
    }
    assert calls["delete"] == 1
    assert calls["stale"] == 2

    with pytest.raises(ActionError, match="choice"):
        production_actions.do_set_reaction_chain_choice(16670, "maybe")
    with pytest.raises(ActionError, match="type_id"):
        production_actions.do_set_reaction_chain_choice(0, "buy")


def test_do_update_settings_marks_stale_only_when_capacity_value_changes(monkeypatch):
    cfg = ProductionConfig(reaction_capacity_enabled=False, reaction_cycle_days=13.0)
    stale = {"n": 0}
    monkeypatch.setattr(
        production_actions, "_mark_cached_production_plans_stale",
        lambda: stale.__setitem__("n", stale["n"] + 1),
    )
    monkeypatch.setattr(
        production_actions, "save_tenant_config_overrides",
        lambda scope, updates, *live, cfg_type=None: apply_config_overrides(cfg, updates),
    )
    monkeypatch.setattr(production_actions, "invalidate_discover_cache", lambda: None)
    monkeypatch.setattr(production_actions, "invalidate_ship_margin_cache", lambda: None)
    monkeypatch.setattr(production_actions, "invalidate_production_locations_cache", lambda: None)
    monkeypatch.setattr(production_actions.dynamic_targets, "invalidate_today", lambda: None)

    # Same value as live cfg → no stale (full form dump still carries the key).
    production_actions.do_update_settings(
        {"reaction_capacity_enabled": False, "min_margin": 0.1}, cfg=cfg,
    )
    assert stale["n"] == 0

    # Real change → stale.
    production_actions.do_update_settings(
        {"reaction_capacity_enabled": True}, cfg=cfg,
    )
    assert stale["n"] == 1
    assert cfg.reaction_capacity_enabled is True


# ------------------------------------------------------------------- routes

def test_put_reaction_chain_choice_route(monkeypatch):
    monkeypatch.setattr(
        production_actions, "do_set_reaction_chain_choice",
        lambda type_id, choice: {"type_id": type_id, "choice": choice},
    )
    resp = client.put(
        "/api/production/reaction-chain-choice",
        json={"type_id": 16670, "choice": "wait"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"type_id": 16670, "choice": "wait"}

    resp = client.put(
        "/api/production/reaction-chain-choice",
        json={"type_id": 16670, "choice": None},
    )
    assert resp.status_code == 200
    assert resp.json()["choice"] is None


def test_put_reaction_chain_choice_action_error_maps_to_400(monkeypatch):
    def _raise(**kwargs):
        raise ActionError("choice must be 'buy', 'wait', or null")
    monkeypatch.setattr(production_actions, "do_set_reaction_chain_choice", _raise)
    resp = client.put(
        "/api/production/reaction-chain-choice",
        json={"type_id": 1, "choice": "wait"},
    )
    assert resp.status_code == 400
    assert "choice" in resp.json()["detail"]


def test_get_reaction_capacity_status_route(monkeypatch):
    monkeypatch.setattr(
        production_actions, "do_get_reaction_capacity_status",
        lambda: {
            "derived_slots": 10, "slot_override": None, "slots_total": 10,
            "slots_busy": 2, "slots_free": 8, "slots_busy_w2": 0,
            "slots_free_w2": 10, "window_next": None, "cycle_days": 13.0,
            "capacity_slot_days": 104.0, "enabled": False,
        },
    )
    resp = client.get("/api/production/settings/reaction-capacity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slots_total"] == 10
    assert body["slots_busy"] == 2
    assert body["capacity_slot_days"] == 104.0


def test_get_reaction_chain_choices_route(monkeypatch):
    monkeypatch.setattr(storage, "load_reaction_chain_choices", lambda: {16670: "wait"})
    resp = client.get("/api/production/reaction-chain-choices")
    assert resp.status_code == 200
    # JSON object keys are strings.
    assert resp.json() == {"16670": "wait"}


# ------------------------------------------------------------------- storage / RLS

pytestmark_pg = pg_helpers.postgres_required()


@pg_helpers.postgres_required()
def test_reaction_chain_choice_rls_isolates_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    pg_helpers.clean_tables(tenant_pair, "reaction_chain_choice")
    try:
        with storage.tenant_context(tenant_a):
            storage.upsert_reaction_chain_choice(16670, "wait")
            assert storage.load_reaction_chain_choices() == {16670: "wait"}
        with storage.tenant_context(tenant_b):
            storage.upsert_reaction_chain_choice(16670, "buy")
            assert storage.load_reaction_chain_choices() == {16670: "buy"}
        with storage.tenant_context(tenant_a):
            assert storage.load_reaction_chain_choices() == {16670: "wait"}
            storage.delete_reaction_chain_choice(16670)
            assert storage.load_reaction_chain_choices() == {}
        with storage.tenant_context(tenant_b):
            assert storage.load_reaction_chain_choices() == {16670: "buy"}
    finally:
        pg_helpers.clean_tables(tenant_pair, "reaction_chain_choice")


@pg_helpers.postgres_required()
def test_do_update_settings_persists_reaction_capacity_fields(tenant):
    from eve_trader.production.config import PRODUCTION_CONFIG

    original = {
        "reaction_capacity_enabled": PRODUCTION_CONFIG.reaction_capacity_enabled,
        "reaction_cycle_days": PRODUCTION_CONFIG.reaction_cycle_days,
        "reaction_window_next": PRODUCTION_CONFIG.reaction_window_next,
        "reaction_slot_override": PRODUCTION_CONFIG.reaction_slot_override,
    }
    try:
        production_actions.do_update_settings({
            "reaction_capacity_enabled": True,
            "reaction_cycle_days": 10.0,
            "reaction_window_next": "2026-11-03T17:00:00+00:00",
            "reaction_slot_override": 42,
        })
        assert PRODUCTION_CONFIG.reaction_capacity_enabled is True
        assert PRODUCTION_CONFIG.reaction_cycle_days == 10.0
        assert PRODUCTION_CONFIG.reaction_window_next == "2026-11-03T17:00:00+00:00"
        assert PRODUCTION_CONFIG.reaction_slot_override == 42
        saved = storage.load_tenant_settings("production")
        assert saved["reaction_capacity_enabled"] is True
        assert saved["reaction_cycle_days"] == 10.0
        assert saved["reaction_slot_override"] == 42
    finally:
        apply_config_overrides(PRODUCTION_CONFIG, original)

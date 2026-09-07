"""Phase F.1 — E.2–E.5 extensions do not interfere with each other."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.production import actions, jobs, preview_refresh

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    pg_helpers.wipe_tables("character_assets", "character_industry_jobs", "character_slots")
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


def test_persist_then_auto_recompute_then_reload(order_sde):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    wrapped = preview_refresh.set_item_and_preview(order_id, fx.FINISHED_A, 14.0, cfg=fx.widget_cfg())
    reloaded = actions.do_get_special_order(order_id)
    compute = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert reloaded["items"][0]["quantity"] == 14.0
    assert fx.fingerprint(wrapped["plan"]) == fx.fingerprint(compute)
    events = [r["event"] for r in actions.do_list_special_order_events(order_id)["rows"]]
    assert events == ["created", "item_set"]


def test_remove_events_reload_delete(order_sde):
    order_id = actions.do_create_special_order([
        {"type_id": fx.FINISHED_A, "quantity": 2.0},
        {"type_id": fx.FINISHED_B, "quantity": 3.0},
    ])["order_id"]
    actions.do_remove_special_order_item(order_id, fx.FINISHED_B)
    actions.do_remove_special_order(order_id)
    events = [r["event"] for r in actions.do_list_special_order_events(order_id)["rows"]]
    assert events[-1] == "deleted"
    assert actions.do_audit_special_orders()["ok"] is True


def test_combined_net_flag_does_not_write_stored_flags(order_sde):
    a = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    b = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_B, "quantity": 10.0}], net_against_stock=True)["order_id"]
    scratch_1 = fx.fingerprint(actions.do_compute_combined_special_orders(
        [a, b], net_against_stock=False, cfg=fx.widget_cfg()))
    actions.do_compute_combined_special_orders([a, b], net_against_stock=True, cfg=fx.widget_cfg())
    scratch_2 = fx.fingerprint(actions.do_compute_combined_special_orders(
        [a, b], net_against_stock=False, cfg=fx.widget_cfg()))
    assert scratch_1 == scratch_2
    assert actions.do_get_special_order(a)["order"].net_against_stock is True
    assert actions.do_get_special_order(b)["order"].net_against_stock is True


def test_slots_and_cost_index_do_not_enter_orders(order_sde, monkeypatch):
    storage.replace_character_slots([("Alice", 5, 3, 2)])
    monkeypatch.setattr(actions, "do_update_settings", lambda updates, cfg=None: updates)
    actions.do_set_cost_index_override("component", 0.05)
    jobs.character_slot_overview()
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])["order_id"]
    events = [r["event"] for r in actions.do_list_special_order_events(order_id)["rows"]]
    assert events == ["created"]
    plan = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert fx.fingerprint(plan) == fx.fingerprint(actions.do_compute_special_order(order_id, cfg=fx.widget_cfg()))

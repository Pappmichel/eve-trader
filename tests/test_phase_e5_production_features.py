"""Phase E.5 — cost-index overrides and ESI slot totals stay outside the core."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions, jobs

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


def test_cost_index_override_roundtrip(order_sde, monkeypatch):
    captured = {}

    def _save(updates, cfg=None):
        captured["updates"] = updates
        return updates

    monkeypatch.setattr(actions, "do_update_settings", lambda updates, cfg=None: _save(updates, cfg))
    actions.do_set_cost_index_override("manufacturing", 0.12)
    assert captured["updates"] == {"manufacturing_cost_index_override": 0.12}
    actions.do_clear_cost_index_override("reaction")
    assert captured["updates"] == {"reaction_cost_index_override": None}
    with pytest.raises(ActionError, match="Unknown cost-index kind"):
        actions.do_set_cost_index_override("bogus", 0.1)


def test_slot_overview_does_not_write_special_orders(order_sde):
    storage.replace_character_slots([("Alice", 5, 3, 2)])
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, 34, fx.FINISHED_A, 1, 60003760, "active", "", "", 1, "Alice"),
    ])
    before = storage.list_special_orders()
    rows = jobs.character_slot_overview()
    assert any(r.character_name == "Alice" and r.total_slots == 5 for r in rows)
    assert storage.list_special_orders() == before
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert "slots" not in plan

"""Router-level tests: verify request/response marshaling for the FastAPI
routes themselves (query params reach the right actions.do_* kwargs, action
results serialize correctly, ActionError maps to HTTP 400) - the layer below
(actions.py/storage.py/engine.py) already has its own tests, these don't
re-test that logic, only the thin wrapper on top of it. Every underlying
storage/action call is monkeypatched, so these never touch the real DB, ESI,
or Goonmetrics - safe to run anywhere, no network/auth required.
"""
from fastapi.testclient import TestClient

from eve_trader import actions, storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.models import ShortlistItem
from eve_trader.production import actions as production_actions
from eve_trader.production.models import AssetLocationRow, ShipMarginRow

client = TestClient(create_app())


# ------------------------------------------------------------------- trading
def test_get_shortlist_items_serializes_storage_rows(monkeypatch):
    monkeypatch.setattr(storage, "load_shortlist", lambda: [
        ShortlistItem(item="Widget", item_id=1, category="Material", volume_m3=0.5, active=True, meta_level=None),
    ])
    resp = client.get("/api/trading/shortlist/items")
    assert resp.status_code == 200
    assert resp.json() == [{
        "item": "Widget", "item_id": 1, "category": "Material",
        "volume_m3": 0.5, "active": True, "meta_level": None,
    }]


def test_refresh_shortlist_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("Shortlist is empty.")
    monkeypatch.setattr(actions, "do_refresh_shortlist", _raise)

    resp = client.post("/api/trading/shortlist/refresh")

    assert resp.status_code == 400
    assert resp.json() == {"detail": "Shortlist is empty."}


def test_get_transaction_characters_serializes_role_tuples(monkeypatch):
    monkeypatch.setattr(actions, "do_list_transaction_characters", lambda: [
        ("buyer:2112625428", 2112625428, "Some Buyer"),
    ])
    resp = client.get("/api/trading/transaction-characters")
    assert resp.status_code == 200
    assert resp.json() == [
        {"role_key": "buyer:2112625428", "character_id": 2112625428, "character_name": "Some Buyer"},
    ]


def test_get_wallet_transactions_passes_role_key_and_lookback_days(monkeypatch):
    captured = {}

    def _fake(role_key, lookback_days=None):
        captured["role_key"] = role_key
        captured["lookback_days"] = lookback_days
        return [{
            "transaction_id": 1, "date": "2026-08-01T00:00:00Z", "type_id": 34,
            "item": "Tritanium", "is_buy": True, "quantity": 100, "unit_price": 5.0,
            "total": 500.0, "location_id": 60003760, "location_name": "Jita IV - Moon 4",
        }]
    monkeypatch.setattr(actions, "do_wallet_transactions", _fake)

    resp = client.get("/api/trading/wallet-transactions?role_key=buyer:2112625428&lookback_days=90")

    assert resp.status_code == 200
    assert captured == {"role_key": "buyer:2112625428", "lookback_days": 90}
    assert resp.json()[0]["item"] == "Tritanium"


def test_get_wallet_transactions_action_error_maps_to_400(monkeypatch):
    def _raise(role_key, lookback_days=None):
        raise ActionError("Character 'buyer:1' is not logged in.")
    monkeypatch.setattr(actions, "do_wallet_transactions", _raise)

    resp = client.get("/api/trading/wallet-transactions?role_key=buyer:1")

    assert resp.status_code == 400
    assert resp.json() == {"detail": "Character 'buyer:1' is not logged in."}


def test_refresh_and_prune_candidates_passes_safe_query_param(monkeypatch):
    captured = {}

    def _capture(safe=True, **kwargs):
        captured["safe"] = safe
        return {"new_candidates_evaluated": 0}
    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", _capture)

    resp = client.post("/api/trading/candidates/refresh-and-prune?safe=false")

    assert resp.status_code == 200
    assert captured["safe"] is False
    assert resp.json() == {"new_candidates_evaluated": 0}


def test_shortlist_snapshot_merges_days_until_deactivation(monkeypatch):
    import pandas as pd

    df = pd.DataFrame([{
        "item": "Widget", "category": "Material", "landed_cost": 100.0, "net_sell": 150.0,
        "sell_volume": 10.0, "own_orders_remaining": 0.0, "profit_per_unit": 50.0, "margin": 0.5,
        "profit_per_m3": 100.0, "decision": "Skip", "active": True, "item_id": 1,
        "volume_m3": 0.5, "jita_sell": 100.0, "import_cost": 0.0, "meta_level": None,
    }])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: df)
    monkeypatch.setattr(actions, "shortlist_skip_deactivation_days", lambda: {1: 12})

    resp = client.get("/api/trading/shortlist/snapshot")

    assert resp.status_code == 200
    assert resp.json()[0]["days_until_deactivation"] == 12


def test_get_trading_settings():
    resp = client.get("/api/trading/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "structure_sell_haircut" in body
    assert "skip_grace_period_days" in body


# ---------------------------------------------------------------- production
def test_get_sde_counts(monkeypatch):
    monkeypatch.setattr(storage, "sde_row_counts", lambda: {"sde_types": 42})
    resp = client.get("/api/production/sde/counts")
    assert resp.status_code == 200
    assert resp.json() == {"sde_types": 42}


def test_get_stock_value_success(monkeypatch):
    monkeypatch.setattr(production_actions, "do_stock_value",
                         lambda: {"total_value": 12345.0, "priced_items": 3, "unpriced_items": 1})
    resp = client.get("/api/production/stock-value")
    assert resp.status_code == 200
    assert resp.json() == {"total_value": 12345.0, "priced_items": 3, "unpriced_items": 1}


def test_get_stock_value_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("Keine Stock-Ziele konfiguriert.")
    monkeypatch.setattr(production_actions, "do_stock_value", _raise)

    resp = client.get("/api/production/stock-value")

    assert resp.status_code == 400
    assert resp.json() == {"detail": "Keine Stock-Ziele konfiguriert."}


def test_set_character_slot_excluded_passes_path_and_body(monkeypatch):
    # GitHub issue #39.
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"character_name": kwargs["character_name"], "excluded": kwargs["excluded"]}
    monkeypatch.setattr(production_actions, "do_set_character_slot_excluded", _capture)

    resp = client.put("/api/production/slots/Some%20Character/excluded", json={"excluded": True})

    assert resp.status_code == 200
    assert captured == {"character_name": "Some Character", "excluded": True}
    assert resp.json() == {"character_name": "Some Character", "excluded": True}


def test_get_owned_blueprints(monkeypatch):
    from eve_trader.production.models import OwnedBlueprintRow
    monkeypatch.setattr(production_actions, "do_list_owned_blueprints", lambda: {"rows": [
        OwnedBlueprintRow(type_id=1, type_name="Rifter Blueprint", is_original=True,
                           quantity=1, material_efficiency=10, time_efficiency=20, runs=None),
    ]})
    resp = client.get("/api/production/blueprints")
    assert resp.status_code == 200
    assert resp.json()[0]["type_name"] == "Rifter Blueprint"
    assert resp.json()[0]["runs"] is None


def test_get_manual_blueprint_copy_costs(monkeypatch):
    # GitHub issue #40.
    from eve_trader.production.models import ManualBlueprintCopyCostRow
    monkeypatch.setattr(production_actions, "do_list_manual_blueprint_copy_costs", lambda: {"rows": [
        ManualBlueprintCopyCostRow(type_id=34, type_name="Tritanium", purchase_cost=1_000_000.0,
                                    runs=10, cost_per_run=100_000.0),
    ]})
    resp = client.get("/api/production/blueprints/manual-copy-costs")
    assert resp.status_code == 200
    assert resp.json() == [{
        "type_id": 34, "type_name": "Tritanium", "purchase_cost": 1_000_000.0,
        "runs": 10, "cost_per_run": 100_000.0,
    }]


def test_add_manual_blueprint_copy_cost_passes_body_fields(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"type_id": 34, "type_name": "Tritanium", "purchase_cost": kwargs["purchase_cost"], "runs": kwargs["runs"]}
    monkeypatch.setattr(production_actions, "do_add_manual_blueprint_copy_cost", _capture)

    resp = client.post("/api/production/blueprints/manual-copy-costs",
                        json={"item_name": "Tritanium", "purchase_cost": 1_000_000.0, "runs": 10})

    assert resp.status_code == 200
    assert captured == {"item_name": "Tritanium", "purchase_cost": 1_000_000.0, "runs": 10}


def test_add_manual_blueprint_copy_cost_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("No exact match for 'Nowhere'. Did you mean: Somewhere?")
    monkeypatch.setattr(production_actions, "do_add_manual_blueprint_copy_cost", _raise)

    resp = client.post("/api/production/blueprints/manual-copy-costs",
                        json={"item_name": "Nowhere", "purchase_cost": 1.0, "runs": 1})

    assert resp.status_code == 400
    assert "Nowhere" in resp.json()["detail"]


def test_remove_manual_blueprint_copy_cost_passes_type_id(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"removed": kwargs["type_id"]}
    monkeypatch.setattr(production_actions, "do_remove_manual_blueprint_copy_cost", _capture)

    resp = client.delete("/api/production/blueprints/manual-copy-costs/34")

    assert resp.status_code == 200
    assert captured == {"type_id": 34}


def test_set_system_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("Solarsystem 'Nowhere' nicht gefunden. Exakter Name?")
    monkeypatch.setattr(production_actions, "do_set_system", _raise)

    resp = client.post(
        "/api/production/settings/systems",
        json={"profile": "component", "system_id": 99999999, "system_name": "Nowhere"},
    )

    assert resp.status_code == 400
    assert "Nowhere" in resp.json()["detail"]


def test_set_system_passes_system_id_through(monkeypatch):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return kwargs
    monkeypatch.setattr(production_actions, "do_set_system", _capture)

    resp = client.post(
        "/api/production/settings/systems",
        json={"profile": "manufacturing", "system_id": 30000142, "system_name": "Jita"},
    )

    assert resp.status_code == 200
    assert captured == {"profile": "manufacturing", "system_id": 30000142, "system_name": "Jita"}


def test_get_system_cost_indices_passes_through_action_result(monkeypatch):
    monkeypatch.setattr(production_actions, "do_get_system_cost_indices", lambda: {
        "component": {"manufacturing": 0.0231, "reaction": 0.0583},
        "manufacturing": None,
    })

    resp = client.get("/api/production/settings/system-cost-indices")

    assert resp.status_code == 200
    assert resp.json() == {"component": {"manufacturing": 0.0231, "reaction": 0.0583}, "manufacturing": None}


def test_get_sde_item_names_serializes_storage_rows(monkeypatch):
    monkeypatch.setattr(storage, "list_all_sde_types", lambda: [(587, "Rifter"), (34, "Tritanium")])

    resp = client.get("/api/production/sde/item-names")

    assert resp.status_code == 200
    assert resp.json() == [
        {"type_id": 587, "type_name": "Rifter"},
        {"type_id": 34, "type_name": "Tritanium"},
    ]


def test_get_all_solar_systems_serializes_storage_rows(monkeypatch):
    monkeypatch.setattr(storage, "list_all_solar_systems", lambda: [(30000142, "Jita"), (30002187, "Amarr")])

    resp = client.get("/api/production/systems")

    assert resp.status_code == 200
    assert resp.json() == [
        {"solar_system_id": 30000142, "solar_system_name": "Jita"},
        {"solar_system_id": 30002187, "solar_system_name": "Amarr"},
    ]


def test_get_structure_names_returns_full_tenant_cache(monkeypatch):
    monkeypatch.setattr(storage, "list_cached_structure_names", lambda: [(1049588174021, "C-J Keepstar"), (123, None)])

    resp = client.get("/api/production/logistics/structure-names")

    assert resp.status_code == 200
    assert resp.json() == {"1049588174021": "C-J Keepstar", "123": None}


def test_asset_plan_is_isolated_per_tenant(monkeypatch):
    # Real bug, confirmed live 2026-08-26: _last_asset_plan used to be a
    # single bare Optional[dict], not tenant-keyed - whichever tenant last
    # refreshed overwrote what *every* tenant saw on this page. Simulates
    # two tenants sharing one process (monkeypatching storage.
    # get_current_tenant, same as AccessGateMiddleware would set per-request)
    # without needing a real Postgres connection.
    monkeypatch.setattr(production_actions, "do_refresh_asset_plan",
                         lambda: {"jobs": 0, "plan": {"jobs": []}})

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")
    refresh_resp = client.post("/api/production/asset-plan/refresh")
    assert refresh_resp.status_code == 200
    assert client.get("/api/production/asset-plan").json() == {"jobs": []}

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-b")
    # tenant-b never refreshed - must see nothing, not tenant-a's plan.
    assert client.get("/api/production/asset-plan").json() is None


def test_plan_is_isolated_per_tenant(monkeypatch):
    monkeypatch.setattr(production_actions, "do_refresh_production", lambda: {
        "plan": {"inventory": [], "buy_list": [], "build_list": [], "invention_list": []},
        "stock_targets": 1, "missing_types": 0, "buy_entries": 0, "build_jobs": 0,
    })

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")
    assert client.post("/api/production/plan/refresh").status_code == 200

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-b")
    assert client.get("/api/production/plan").json() is None
    # tenant-b's own logistics reads must also not see tenant-a's build_list.
    resp = client.get("/api/production/logistics")
    assert resp.status_code == 400


# --------------------------------------------------- trend/undercut/discovery
def test_get_shortlist_trends(monkeypatch):
    monkeypatch.setattr(actions, "do_shortlist_trends", lambda: {
        205: {"recent_avg_margin": 0.15, "baseline_avg_margin": 0.10, "trend_pct": 0.5},
    })
    resp = client.get("/api/trading/shortlist/trends")
    assert resp.status_code == 200
    assert resp.json() == {"205": {"recent_avg_margin": 0.15, "baseline_avg_margin": 0.10, "trend_pct": 0.5}}


def test_check_undercut_success(monkeypatch):
    from eve_trader.models import UndercutRow
    monkeypatch.setattr(actions, "do_check_undercut", lambda: {"rows": [
        UndercutRow(type_id=205, item="Nova Cruise Missile", my_price=100.0, competitor_price=90.0, difference=10.0),
    ]})
    resp = client.post("/api/trading/seller/undercut")
    assert resp.status_code == 200
    assert resp.json() == [{"type_id": 205, "item": "Nova Cruise Missile",
                             "my_price": 100.0, "competitor_price": 90.0, "difference": 10.0}]


def test_check_undercut_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("Seller character isn't logged in yet (Login → Seller).")
    monkeypatch.setattr(actions, "do_check_undercut", _raise)

    resp = client.post("/api/trading/seller/undercut")

    assert resp.status_code == 400
    assert "Seller" in resp.json()["detail"]


def test_check_seller_unlisted_stock_includes_sell_volume_and_margin(monkeypatch):
    # GitHub issue #56: sell_volume/margin were added to the underlying
    # dataclass and actions.py by issue #45, but never added to
    # schemas.UnlistedStockRow - FastAPI's response_model silently stripped
    # both fields from the real JSON response even though every layer below
    # the router computed them correctly. This test exercises the actual
    # response_model serialization path (unlike test_check_seller_unlisted_
    # stock_* tests calling do_check_seller_unlisted_stock directly), so it
    # would have caught the regression.
    from eve_trader.models import UnlistedStockRow
    monkeypatch.setattr(actions, "do_check_seller_unlisted_stock", lambda: {"rows": [
        UnlistedStockRow(type_id=205, item="Nova Cruise Missile", asset_quantity=50.0,
                          sell_order_remaining=0.0, unlisted_quantity=50.0,
                          sell_volume=12.0, margin=0.25),
    ]})
    resp = client.post("/api/trading/seller/unlisted-stock")
    assert resp.status_code == 200
    assert resp.json() == [{
        "type_id": 205, "item": "Nova Cruise Missile", "asset_quantity": 50.0,
        "sell_order_remaining": 0.0, "unlisted_quantity": 50.0,
        "sell_volume": 12.0, "margin": 0.25,
    }]


def test_production_unlisted_stock_includes_sell_volume_and_margin(monkeypatch):
    # GitHub issue #56 - same regression, Production side.
    from eve_trader.production.models import UnlistedStockRow as ProductionUnlistedStockRow
    monkeypatch.setattr(production_actions, "do_unlisted_stock", lambda: {"rows": [
        ProductionUnlistedStockRow(type_id=205, type_name="Nova Cruise Missile", stock_quantity=50.0,
                                    sell_volume=12.0, margin=0.25),
    ]})
    resp = client.post("/api/production/unlisted-stock/check")
    assert resp.status_code == 200
    assert resp.json() == [{
        "type_id": 205, "type_name": "Nova Cruise Missile", "stock_quantity": 50.0,
        "sell_volume": 12.0, "margin": 0.25,
    }]


def test_get_sde_freshness(monkeypatch):
    monkeypatch.setattr(production_actions, "do_check_sde_freshness", lambda: {
        "local_refreshed_at": "2026-07-17T07:56:26.701476+00:00",
        "remote_check_succeeded": True, "newer_sde_available": False,
        "trading_universe_stale": False, "trading_universe_built_at": "2026-07-17T07:55:58",
    })
    resp = client.get("/api/production/sde/freshness")
    assert resp.status_code == 200
    assert resp.json()["newer_sde_available"] is False
    assert resp.json()["trading_universe_stale"] is False


def test_discover_build_candidates_passes_top_n_query_param(monkeypatch):
    captured = {}

    def _capture(top_n=200, **kwargs):
        captured["top_n"] = top_n
        return {"rows": []}
    monkeypatch.setattr(production_actions, "do_discover_build_candidates", _capture)

    resp = client.post("/api/production/build-candidates/discover?top_n=50")

    assert resp.status_code == 200
    assert captured["top_n"] == 50
    assert resp.json() == []


def test_discover_build_candidates_defaults_top_n_to_200(monkeypatch):
    captured = {}

    def _capture(top_n=200, **kwargs):
        captured["top_n"] = top_n
        return {"rows": []}
    monkeypatch.setattr(production_actions, "do_discover_build_candidates", _capture)

    resp = client.post("/api/production/build-candidates/discover")

    assert resp.status_code == 200
    assert captured["top_n"] == 200


def test_discover_build_candidates_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("SDE cache is empty. Refresh SDE first.")
    monkeypatch.setattr(production_actions, "do_discover_build_candidates", _raise)

    resp = client.post("/api/production/build-candidates/discover")

    assert resp.status_code == 400
    assert "SDE cache is empty" in resp.json()["detail"]


def test_get_ship_margins_serializes_action_result(monkeypatch):
    monkeypatch.setattr(production_actions, "do_get_ship_margins", lambda **kwargs: {"rows": [
        ShipMarginRow(type_id=1, type_name="Rifter", activity="Tech I", home_price=1000.0, jita_price=900.0,
                      build_cost=500.0, margin_home=1.0, margin_jita=0.8, meta_level=None),
    ]})

    resp = client.get("/api/production/margins")

    assert resp.status_code == 200
    assert resp.json() == [{
        "type_id": 1, "type_name": "Rifter", "activity": "Tech I", "home_price": 1000.0, "jita_price": 900.0,
        "build_cost": 500.0, "margin_home": 1.0, "margin_jita": 0.8, "meta_level": None,
    }]


def test_get_ship_margins_action_error_maps_to_400(monkeypatch):
    def _raise(*args, **kwargs):
        raise ActionError("SDE cache is empty. Refresh SDE first.")
    monkeypatch.setattr(production_actions, "do_get_ship_margins", _raise)

    resp = client.get("/api/production/margins")

    assert resp.status_code == 400
    assert "SDE cache is empty" in resp.json()["detail"]


def test_search_item_margin_passes_item_name(monkeypatch):
    captured = {}

    def _capture(item_name):
        captured["item_name"] = item_name
        return ShipMarginRow(type_id=1, type_name=item_name, activity="Tech I", home_price=1000.0,
                              jita_price=None, build_cost=500.0, margin_home=1.0, margin_jita=None, meta_level=None)
    monkeypatch.setattr(production_actions, "do_get_item_margin", _capture)

    resp = client.post("/api/production/margins/search", json={"item_name": "Rifter"})

    assert resp.status_code == 200
    assert captured == {"item_name": "Rifter"}
    assert resp.json()["type_name"] == "Rifter"


def test_search_item_margin_action_error_maps_to_400(monkeypatch):
    def _raise(item_name):
        raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
    monkeypatch.setattr(production_actions, "do_get_item_margin", _raise)

    resp = client.post("/api/production/margins/search", json={"item_name": "Nonexistent Thing"})

    assert resp.status_code == 400
    assert "Nonexistent Thing" in resp.json()["detail"]


def test_get_material_tree_passes_type_name_and_quantity(monkeypatch):
    captured = {}

    def _capture(type_name, quantity):
        captured["type_name"] = type_name
        captured["quantity"] = quantity
        return {"type_id": 1, "type_name": type_name, "quantity": quantity,
                "activity": "Tech I", "decryptor": None, "children": []}
    monkeypatch.setattr(production_actions, "do_build_material_tree", _capture)

    resp = client.post("/api/production/material-tree", json={"type_name": "Rifter", "quantity": 3})

    assert resp.status_code == 200
    assert captured == {"type_name": "Rifter", "quantity": 3.0}
    assert resp.json()["type_name"] == "Rifter"
    assert resp.json()["quantity"] == 3.0


def test_get_material_tree_defaults_quantity_to_one(monkeypatch):
    captured = {}

    def _capture(type_name, quantity):
        captured["quantity"] = quantity
        return {"type_id": 1, "type_name": type_name, "quantity": quantity,
                "activity": "Tech I", "decryptor": None, "children": []}
    monkeypatch.setattr(production_actions, "do_build_material_tree", _capture)

    resp = client.post("/api/production/material-tree", json={"type_name": "Rifter"})

    assert resp.status_code == 200
    assert captured["quantity"] == 1.0


def test_get_material_tree_action_error_maps_to_400(monkeypatch):
    def _raise(type_name, quantity):
        raise ActionError(f"No type found for '{type_name}'. Refresh SDE first?")
    monkeypatch.setattr(production_actions, "do_build_material_tree", _raise)

    resp = client.post("/api/production/material-tree", json={"type_name": "Nonexistent Thing"})

    assert resp.status_code == 400
    assert "Nonexistent Thing" in resp.json()["detail"]


def test_search_asset_locations_passes_item_name_and_serializes_result(monkeypatch):
    captured = {}

    def _capture(item_name):
        captured["item_name"] = item_name
        return {"type_id": 34, "type_name": "Tritanium", "locations": [
            AssetLocationRow(location_id=1000000000001, location_name="Some Station", owner_name="Alice", quantity=100.0),
        ]}
    monkeypatch.setattr(production_actions, "do_search_item_locations", _capture)

    resp = client.post("/api/production/asset-locations", json={"item_name": "Tritanium"})

    assert resp.status_code == 200
    assert captured == {"item_name": "Tritanium"}
    body = resp.json()
    assert body["type_name"] == "Tritanium"
    assert body["locations"] == [{
        "location_id": 1000000000001, "location_name": "Some Station", "owner_name": "Alice", "quantity": 100.0,
    }]


def test_search_asset_locations_action_error_maps_to_400(monkeypatch):
    def _raise(item_name):
        raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
    monkeypatch.setattr(production_actions, "do_search_item_locations", _raise)

    resp = client.post("/api/production/asset-locations", json={"item_name": "Nonexistent Thing"})

    assert resp.status_code == 400
    assert "Nonexistent Thing" in resp.json()["detail"]


# ------------------------------------------------------------------ portfolio
def test_get_portfolio_overview(monkeypatch):
    from eve_trader import portfolio
    monkeypatch.setattr(portfolio, "portfolio_overview", lambda: {
        "trading_realized_profit": 1000.0, "trading_average_margin": 0.2,
        "trading_daily_profit_volatility": None, "trading_trade_count": 5,
        "production_stock_value": 2000.0, "production_stock_targets_configured": True,
        "combined_value": 3000.0,
    })
    resp = client.get("/api/portfolio/overview")
    assert resp.status_code == 200
    assert resp.json()["combined_value"] == 3000.0
    assert resp.json()["trading_daily_profit_volatility"] is None


def test_get_scheduler_status(monkeypatch):
    from eve_trader import scheduler
    monkeypatch.setattr(scheduler, "get_status", lambda: {
        "enabled": True, "running": True,
        "jobs": {
            "trading_pipeline": {"interval_hours": 24.0, "last_run_at": "2026-07-17T08:06:35", "last_error": None},
            "production_sync": {"interval_hours": 6.0, "last_run_at": None, "last_error": "no auth"},
        },
    })
    resp = client.get("/api/portfolio/scheduler-status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["jobs"]["production_sync"]["last_error"] == "no auth"


def test_get_backups(monkeypatch):
    from eve_trader import actions
    monkeypatch.setattr(actions, "do_list_backups", lambda: {"rows": [
        {"name": "eve_trader_backup_x.zip", "created_at": "2026-07-17T08:00:00+00:00", "size_bytes": 1234},
    ]})
    resp = client.get("/api/portfolio/backups")
    assert resp.status_code == 200
    assert resp.json()[0]["name"] == "eve_trader_backup_x.zip"


def test_create_backup(monkeypatch):
    from eve_trader import actions
    monkeypatch.setattr(actions, "do_create_backup", lambda: {
        "name": "eve_trader_backup_x.zip", "created_at": "2026-07-17T08:00:00+00:00", "size_bytes": 1234,
    })
    resp = client.post("/api/portfolio/backups")
    assert resp.status_code == 200
    assert resp.json()["size_bytes"] == 1234


def test_create_backup_action_error_maps_to_400(monkeypatch):
    from eve_trader.actions import ActionError
    from eve_trader import actions

    def boom():
        raise ActionError("Backup failed: disk full")
    monkeypatch.setattr(actions, "do_create_backup", boom)

    resp = client.post("/api/portfolio/backups")
    assert resp.status_code == 400
    assert "disk full" in resp.json()["detail"]


# ------------------------------------------------------------------------ auth
def test_auth_callback_network_failure_redirects_with_error_instead_of_500(monkeypatch):
    # Real gap confirmed live (2026-08-16): callback() used to only catch
    # requests.HTTPError (the raise_for_status 4xx/5xx case) around the
    # token exchange - a network-level failure (ConnectionError/Timeout,
    # siblings of HTTPError under RequestException, not subclasses of it)
    # escaped entirely, defeating the whole point of this route (always
    # redirect back to the frontend, even on failure) with a raw 500 in the
    # middle of the SSO redirect the user's browser just landed on.
    import time
    import requests
    from eve_trader.api.routers import auth as auth_router
    from eve_trader.auth import TokenManager

    state = "test-state-network-failure"
    auth_router._pending[state] = {
        "verifier": "v", "role_prefix": "producer", "scopes": ["esi-assets.read_assets.v1"],
        "created_at": time.time(), "browser_nonce": "n",
    }

    def _raise(self, code, verifier):
        raise requests.ConnectionError("network blip")
    monkeypatch.setattr(TokenManager, "_exchange_code", _raise)

    resp = client.get(
        "/api/auth/callback", params={"code": "abc", "state": state},
        cookies={auth_router._OAUTH_NONCE_COOKIE: "n"}, follow_redirects=False,
    )

    assert resp.status_code in (302, 307)
    assert "auth=error" in resp.headers["location"]

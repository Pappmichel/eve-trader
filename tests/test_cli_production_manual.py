"""docs/MANUAL_TRACKING_PLAN.md phase 9 - `eve-trader production manual ...`
CLI group. Click's CliRunner, every production_actions.do_* call
monkeypatched (same "never touches real Postgres/ESI" shape as the router
tests) - main()'s own group callback and tenant_scope.enter_tenant (both
real DB reads via config.resolve_and_set_trading_config/
resolve_and_set_production_config) are stubbed out too."""
import contextlib

import pytest
from click.testing import CliRunner

from eve_trader import cli, config, storage, tenant_scope
from eve_trader.actions import ActionError
from eve_trader.production import actions as production_actions
from eve_trader.production import config as production_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_tenant_resolution(monkeypatch):
    monkeypatch.setattr(storage, "set_current_tenant", lambda tenant_id: None)
    monkeypatch.setattr(config, "resolve_and_set_trading_config", lambda tenant_id: None)
    monkeypatch.setattr(production_config, "resolve_and_set_production_config", lambda tenant_id: None)

    @contextlib.contextmanager
    def _fake_enter_tenant(tenant_id):
        yield

    monkeypatch.setattr(cli.tenant_scope, "enter_tenant", _fake_enter_tenant)


def test_manual_stock_list_prints_every_entry(monkeypatch):
    monkeypatch.setattr(production_actions, "do_list_manual_stock_entries", lambda: {"rows": [
        {"type_id": 100, "type_name": "Tritanium", "location_id": 1035466617946, "count": 500.0},
    ]})

    result = runner.invoke(cli.main, ["production", "manual", "stock", "list"])

    assert result.exit_code == 0
    assert "Tritanium" in result.output
    assert "1035466617946" in result.output


def test_manual_stock_add_passes_args_and_prints_result(monkeypatch):
    captured = {}

    def _add(item_name, count, location_id):
        captured.update(item_name=item_name, count=count, location_id=location_id)
        return {"type_id": 100, "type_name": "Tritanium", "location_id": location_id, "count": count}
    monkeypatch.setattr(production_actions, "do_add_manual_stock_entry", _add)

    result = runner.invoke(cli.main, [
        "production", "manual", "stock", "add", "Tritanium", "500", "--location", "1035466617946",
    ])

    assert result.exit_code == 0
    assert captured == {"item_name": "Tritanium", "count": 500.0, "location_id": 1035466617946}
    assert "Tritanium" in result.output


def test_manual_stock_add_action_error_exits_nonzero(monkeypatch):
    monkeypatch.setattr(production_actions, "do_add_manual_stock_entry",
                         lambda item_name, count, location_id: (_ for _ in ()).throw(
                             ActionError("No type found for 'Nonsense'.")))

    result = runner.invoke(cli.main, ["production", "manual", "stock", "add", "Nonsense", "1"])

    assert result.exit_code == 1
    assert "No type found" in result.output


def test_manual_stock_remove_passes_type_and_location(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_remove_manual_stock_entry",
                         lambda type_id, location_id: captured.update(type_id=type_id, location_id=location_id))

    result = runner.invoke(cli.main, [
        "production", "manual", "stock", "remove", "100", "--location", "1035466617946",
    ])

    assert result.exit_code == 0
    assert captured == {"type_id": 100, "location_id": 1035466617946}


def test_manual_stock_import_dry_run_reads_stdin_and_previews(monkeypatch):
    captured = {}

    def _preview(text, location_id, mode):
        captured.update(text=text, location_id=location_id, mode=mode)
        return {"rows": [{"type_id": 100, "name": "Tritanium", "old": 0.0, "new": 500.0, "status": "new"}],
                "skipped_blueprints": [], "unresolved": [], "errors": []}
    monkeypatch.setattr(production_actions, "do_preview_asset_paste", _preview)

    result = runner.invoke(cli.main, [
        "production", "manual", "stock", "import", "-",
        "--location", "1035466617946", "--mode", "merge", "--dry-run",
    ], input="Tritanium\t500\n")

    assert result.exit_code == 0
    assert captured["location_id"] == 1035466617946
    assert captured["mode"] == "merge"
    assert "Tritanium" in captured["text"]
    assert "[new] Tritanium" in result.output


def test_manual_stock_import_commit_calls_commit_action(monkeypatch):
    monkeypatch.setattr(production_actions, "do_preview_asset_paste",
                         lambda text, location_id, mode: pytest.fail("dry-run action must not be called"))
    monkeypatch.setattr(production_actions, "do_commit_asset_paste", lambda text, location_id, mode: {
        "applied": 1, "skipped_blueprints": [], "unresolved": [], "errors": [],
    })

    result = runner.invoke(cli.main, [
        "production", "manual", "stock", "import", "-",
        "--location", "1035466617946", "--mode", "replace",
    ], input="Tritanium\t500\n")

    assert result.exit_code == 0
    assert "Applied" in result.output


def test_manual_blueprints_list_marks_source_and_manual_id(monkeypatch):
    from eve_trader.production.models import OwnedBlueprintRow
    monkeypatch.setattr(production_actions, "do_list_owned_blueprints", lambda: {"rows": [
        OwnedBlueprintRow(type_id=100, type_name="Rifter Blueprint", is_original=True, quantity=1,
                           material_efficiency=10, time_efficiency=20, runs=None,
                           source="manual", manual_id=7, location_id=1035466617946),
    ]})

    result = runner.invoke(cli.main, ["production", "manual", "blueprints", "list"])

    assert result.exit_code == 0
    assert "id=7" in result.output
    assert "Rifter Blueprint" in result.output


def test_manual_blueprints_add_passes_args(monkeypatch):
    captured = {}

    def _add(item_name, is_original, material_efficiency, time_efficiency, runs, quantity, location_id):
        captured.update(item_name=item_name, is_original=is_original, material_efficiency=material_efficiency,
                         time_efficiency=time_efficiency, runs=runs, quantity=quantity, location_id=location_id)
        return {"manual_id": 7, "type_id": 100, "type_name": "Rifter Blueprint"}
    monkeypatch.setattr(production_actions, "do_add_manual_owned_blueprint", _add)

    result = runner.invoke(cli.main, [
        "production", "manual", "blueprints", "add", "Rifter Blueprint",
        "--original", "--me", "10", "--te", "20", "--quantity", "1", "--location", "1035466617946",
    ])

    assert result.exit_code == 0
    assert captured == {
        "item_name": "Rifter Blueprint", "is_original": True, "material_efficiency": 10,
        "time_efficiency": 20, "runs": None, "quantity": 1, "location_id": 1035466617946,
    }
    assert "id=7" in result.output


def test_manual_blueprints_remove_passes_manual_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_remove_manual_owned_blueprint",
                         lambda manual_id: captured.update(manual_id=manual_id))

    result = runner.invoke(cli.main, ["production", "manual", "blueprints", "remove", "7"])

    assert result.exit_code == 0
    assert captured == {"manual_id": 7}


def test_manual_jobs_add_passes_quantity_or_runs(monkeypatch):
    captured = {}

    def _add(item_name, quantity=None, runs=None, location_id=0, ready_at=None):
        captured.update(item_name=item_name, quantity=quantity, runs=runs,
                         location_id=location_id, ready_at=ready_at)
        return {"manual_id": 3, "type_id": 100, "type_name": "Tritanium"}
    monkeypatch.setattr(production_actions, "do_add_manual_industry_job", _add)

    result = runner.invoke(cli.main, [
        "production", "manual", "jobs", "add", "Tritanium", "--runs", "5", "--location", "1035466617946",
    ])

    assert result.exit_code == 0
    assert captured == {
        "item_name": "Tritanium", "quantity": None, "runs": 5,
        "location_id": 1035466617946, "ready_at": None,
    }
    assert "id=3" in result.output


def test_manual_jobs_complete_uses_job_location_by_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_complete_manual_industry_job",
                         lambda manual_id, location_id: captured.update(
                             manual_id=manual_id, location_id=location_id) or {"manual_id": manual_id})

    result = runner.invoke(cli.main, ["production", "manual", "jobs", "complete", "3"])

    assert result.exit_code == 0
    assert captured == {"manual_id": 3, "location_id": None}


def test_manual_jobs_remove_passes_manual_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_remove_manual_industry_job",
                         lambda manual_id: captured.update(manual_id=manual_id))

    result = runner.invoke(cli.main, ["production", "manual", "jobs", "remove", "3"])

    assert result.exit_code == 0
    assert captured == {"manual_id": 3}


def test_manual_listed_set_validates_market_choice():
    result = runner.invoke(cli.main, ["production", "manual", "listed", "set", "100", "nowhere", "5"])

    assert result.exit_code != 0
    assert "nowhere" in result.output


def test_manual_listed_set_passes_args(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_set_manual_listed_stock",
                         lambda type_id, market, quantity: captured.update(
                             type_id=type_id, market=market, quantity=quantity))

    result = runner.invoke(cli.main, ["production", "manual", "listed", "set", "100", "home", "5"])

    assert result.exit_code == 0
    assert captured == {"type_id": 100, "market": "home", "quantity": 5.0}


def test_manual_listed_clear_passes_args(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_clear_manual_listed_stock",
                         lambda type_id, market: captured.update(type_id=type_id, market=market))

    result = runner.invoke(cli.main, ["production", "manual", "listed", "clear", "100", "jita"])

    assert result.exit_code == 0
    assert captured == {"type_id": 100, "market": "jita"}


def test_manual_locations_name_passes_args(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_set_manual_location_name",
                         lambda location_id, name: captured.update(location_id=location_id, name=name))

    result = runner.invoke(cli.main, [
        "production", "manual", "locations", "name", "1035466617946", "C-J Keepstar",
    ])

    assert result.exit_code == 0
    assert captured == {"location_id": 1035466617946, "name": "C-J Keepstar"}


def test_manual_locations_unname_passes_location_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_remove_manual_location_name",
                         lambda location_id: captured.update(location_id=location_id))

    result = runner.invoke(cli.main, ["production", "manual", "locations", "unname", "1035466617946"])

    assert result.exit_code == 0
    assert captured == {"location_id": 1035466617946}


def test_tenant_id_option_passed_to_enter_tenant(monkeypatch):
    seen_tenant_ids = []

    @contextlib.contextmanager
    def _capturing_enter_tenant(tenant_id):
        seen_tenant_ids.append(tenant_id)
        yield
    monkeypatch.setattr(cli.tenant_scope, "enter_tenant", _capturing_enter_tenant)
    monkeypatch.setattr(production_actions, "do_list_manual_stock_entries", lambda: {"rows": []})

    result = runner.invoke(cli.main, [
        "production", "manual", "stock", "list", "--tenant-id", "some-other-tenant",
    ])

    assert result.exit_code == 0
    assert seen_tenant_ids == ["some-other-tenant"]


def test_tenant_id_option_defaults_to_default_tenant(monkeypatch):
    seen_tenant_ids = []

    @contextlib.contextmanager
    def _capturing_enter_tenant(tenant_id):
        seen_tenant_ids.append(tenant_id)
        yield
    monkeypatch.setattr(cli.tenant_scope, "enter_tenant", _capturing_enter_tenant)
    monkeypatch.setattr(production_actions, "do_list_manual_stock_entries", lambda: {"rows": []})

    result = runner.invoke(cli.main, ["production", "manual", "stock", "list"])

    assert result.exit_code == 0
    assert seen_tenant_ids == [storage.DEFAULT_TENANT_ID]


def test_max_build_prints_quantity_bottleneck_and_requirements(monkeypatch):
    captured = {}

    def _max(item, mode, target_quantity):
        captured.update(item=item, mode=mode, target_quantity=target_quantity)
        row = {"type_id": 3, "type_name": "Mineral", "depth": 1, "needed": 10050.0, "on_hand": 10000.0,
               "from_stock": 10000.0, "built": 0.0, "runs": 0, "short": 50.0, "reason": "material",
               "blueprint": None}
        return {
            "type_name": "Ship", "mode": mode, "max_quantity": 66, "capped": False,
            "blueprint": {"source": "copies", "copy_runs": 80},
            "bottleneck_quantity": 67, "bottlenecks": [row],
            "requirements_quantity": 66, "requirements_feasible": True, "requirements": [row],
        }
    monkeypatch.setattr(production_actions, "do_max_buildable", _max)

    result = runner.invoke(cli.main, ["production", "max-build", "Ship", "--mode", "direct"])

    assert result.exit_code == 0, result.output
    assert captured == {"item": "Ship", "mode": "direct", "target_quantity": None}
    assert "Max buildable now: 66" in result.output
    assert "copies, 80 runs" in result.output
    assert "Mineral" in result.output and "not on hand" in result.output


def test_max_build_action_error_exits_1(monkeypatch):
    def _raise(*a, **kw):
        raise ActionError("No type found for 'Nope'.")
    monkeypatch.setattr(production_actions, "do_max_buildable", _raise)

    result = runner.invoke(cli.main, ["production", "max-build", "Nope"])

    assert result.exit_code == 1
    assert "No type found" in result.output


def test_facilities_list_prints_effective_rigs_and_sources(monkeypatch):
    row = {
        "category": "Large Ships", "structure_name": "IAK-JW - CEZ T2 Adv L T1 Basic L", "split_by_tech": True,
        "effective": {"structure_type": "Raitaru (M Engineering Complex)", "structure_source": "global",
                      "rig_tier_basic": "T1-Rig", "rig_source_basic": "name",
                      "rig_tier_advanced": "T2-Rig", "rig_source_advanced": "name", "security_multiplier": 2.1},
    }
    monkeypatch.setattr(production_actions, "do_list_category_facilities", lambda: {"categories": [row]})

    result = runner.invoke(cli.main, ["production", "facilities", "list"])

    assert result.exit_code == 0, result.output
    assert "T1-Rig (name)" in result.output and "T2-Rig (name)" in result.output
    assert "IAK-JW - CEZ T2 Adv L T1 Basic L" in result.output


def test_facilities_set_passes_options(monkeypatch):
    captured = {}

    def _set(category, structure_type=None, rig_tier_basic=None, rig_tier_advanced=None,
             facility_tax_rate=None):
        captured.update(category=category, structure_type=structure_type,
                        rig_tier_basic=rig_tier_basic, rig_tier_advanced=rig_tier_advanced,
                        facility_tax_rate=facility_tax_rate)
        return captured
    monkeypatch.setattr(production_actions, "do_set_category_facility", _set)

    result = runner.invoke(cli.main, ["production", "facilities", "set", "Large Ships",
                                      "--rig", "T1-Rig", "--rig-advanced", "T2-Rig"])

    assert result.exit_code == 0, result.output
    assert captured == {"category": "Large Ships", "structure_type": None,
                        "rig_tier_basic": "T1-Rig", "rig_tier_advanced": "T2-Rig",
                        "facility_tax_rate": None}


def test_facilities_set_tax_percent_is_passed_as_a_fraction(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_actions, "do_set_category_facility",
                        lambda category, **kwargs: captured.update(category=category, **kwargs))

    result = runner.invoke(cli.main, ["production", "facilities", "set", "Large Ships", "--tax-percent", "0.5"])

    assert result.exit_code == 0, result.output
    assert captured["facility_tax_rate"] == pytest.approx(0.005)


def test_facilities_list_prints_the_facility_tax_source_and_age(monkeypatch):
    row = {
        "category": "Large Ships", "structure_name": "X", "split_by_tech": False,
        "effective": {"structure_type": "Raitaru (M Engineering Complex)", "structure_source": "global",
                      "rig_tier_basic": "T1-Rig", "rig_source_basic": "name",
                      "rig_tier_advanced": None, "rig_source_advanced": None, "security_multiplier": 1.0},
        "tax": {"saved": None, "effective": 0.005, "source": "observed", "settings_rate": 0.0025,
                "observed": {"rate": 0.005, "observed_at": "2026-11-05T10:00:00+00:00", "age_days": 2.4, "jobs": 3}},
    }
    monkeypatch.setattr(production_actions, "do_list_category_facilities", lambda: {"categories": [row]})

    result = runner.invoke(cli.main, ["production", "facilities", "list"])

    assert result.exit_code == 0, result.output
    assert "0.50% (observed, 3 jobs, 2d ago)" in result.output

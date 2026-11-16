"""Settings saves write only the fields a client sent (partial update models),
and the boot load applies every config scope."""
import pytest
from fastapi.testclient import TestClient

from eve_trader import scheduler, storage, tenant_scope
from eve_trader.api import app as app_module
from eve_trader.api.app import create_app
from eve_trader.config import TRADING_CONFIG, apply_config_overrides
from eve_trader.doctrine.config import DOCTRINE_CONFIG
from eve_trader.module_reprocessing.config import MODULE_REPROCESSING_CONFIG
from eve_trader.pi.config import PI_CONFIG
from eve_trader.production.config import PRODUCTION_CONFIG
from eve_trader.refining.config import REFINING_CONFIG
from eve_trader.station_trading.config import STATION_TRADING_CONFIG

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema  # noqa: F401

pytestmark = pg_helpers.postgres_required()

client = TestClient(create_app())
DEFAULT = storage.DEFAULT_TENANT_ID

SCOPES = ("trading", "production", "doctrine", "pi", "refining", "station_trading", "module_reprocessing")


@pytest.fixture(autouse=True)
def _default_tenant_settings(monkeypatch):
    """The gate-off HTTP path runs as the default tenant: start and end clean."""
    monkeypatch.setattr(scheduler, "start", lambda: None)
    pg_helpers.wipe_tables("tenant_settings")
    with tenant_scope.enter_tenant(DEFAULT):
        yield
    pg_helpers.wipe_tables("tenant_settings")


def _stored(scope):
    with storage.tenant_context(DEFAULT):
        return storage.load_tenant_settings(scope)


_LIVE = {
    "trading": TRADING_CONFIG, "production": PRODUCTION_CONFIG, "doctrine": DOCTRINE_CONFIG, "pi": PI_CONFIG,
    "refining": REFINING_CONFIG, "station_trading": STATION_TRADING_CONFIG,
    "module_reprocessing": MODULE_REPROCESSING_CONFIG,
}


def _seed(scope, values, live=True):
    """Stores `values` as the tenant's saved settings and, like a real earlier
    save would have, puts them on the live config the request resolves."""
    with storage.tenant_context(DEFAULT):
        storage.save_tenant_settings(scope, values)
    if live:
        apply_config_overrides(_LIVE[scope], values)


def test_trading_partial_save_keeps_everything_else():
    seed = {"scheduler_enabled": True, "esi_rare_interval_hours": 48.0, "min_paired_days": 9}
    _seed("trading", seed)
    resp = client.post("/api/trading/settings", json={"min_margin_threshold": 0.2})
    assert resp.status_code == 200
    assert _stored("trading") == {**seed, "min_margin_threshold": 0.2}


def test_trading_explicit_null_on_a_nullable_field_is_kept():
    _seed("trading", {"structure_id": 123})
    assert client.post("/api/trading/settings", json={"structure_id": None}).status_code == 200
    assert _stored("trading") == {"structure_id": None}


def test_trading_full_form_still_works_and_wrong_type_is_422():
    form = client.get("/api/trading/settings").json()
    assert client.post("/api/trading/settings", json=form).status_code == 200
    assert set(_stored("trading")) == set(form)
    assert client.post("/api/trading/settings", json={"min_margin_threshold": "lots"}).status_code == 422
    assert client.post("/api/trading/settings", json={"min_margin_threshold": None}).status_code == 422


def test_production_partial_save_and_merged_validation():
    seed = {"dynamic_targets_enabled": True, "dynamic_target_reference_margin": 0.3, "market_fees": 0.05}
    _seed("production", seed)
    assert client.post("/api/production/settings", json={"haul_cost_per_m3": 900.0}).status_code == 200
    assert _stored("production") == {**seed, "haul_cost_per_m3": 900.0}
    # The reference margin lives in the stored config, not in this body: the
    # check runs against stored values plus the update.
    resp = client.post("/api/production/settings", json={"min_margin": 0.5})
    assert resp.status_code == 400
    assert "dynamic_target_reference_margin" in resp.json()["detail"]
    assert "min_margin" not in _stored("production")


def test_station_trading_partial_save():
    seed = {"broker_fee_rate": 0.02, "station_id": 60003760}
    _seed("station_trading", seed)
    assert client.post("/api/station-trading/settings", json={"sales_tax_rate": 0.04}).status_code == 200
    assert _stored("station_trading") == {**seed, "sales_tax_rate": 0.04}
    assert client.post("/api/station-trading/settings", json={"hub_region_id": 0}).status_code == 400


def test_refining_partial_save():
    seed = {"implant": "None", "reprocessing_skill_level": 4}
    _seed("refining", seed)
    assert client.post("/api/refining/settings", json={"refining_tax_rate": 0.03}).status_code == 200
    assert _stored("refining") == {**seed, "refining_tax_rate": 0.03}
    assert client.post("/api/refining/settings", json={"rig_tier": "not a tier"}).status_code == 400


def test_module_reprocessing_partial_save():
    seed = {"freight_cost_per_m3": 700.0, "ignore_thresholds": True}
    _seed("module_reprocessing", seed)
    assert client.post("/api/module-reprocessing/settings", json={"min_profit_threshold": 5.0}).status_code == 200
    assert _stored("module_reprocessing") == {**seed, "min_profit_threshold": 5.0}


def test_doctrine_partial_save_and_hangar_group_validated_as_stored():
    seed = {"cargo_tolerance_pct": 7.0, "strategic_reserve_owner_type": "character",
            "strategic_reserve_owner_id": 1, "strategic_reserve_location_id": 2,
            "strategic_reserve_hangar_flag": "Hangar"}
    _seed("doctrine", seed)
    assert client.post("/api/doctrine/settings", json={"strict_extras": True}).status_code == 200
    assert _stored("doctrine") == {**seed, "strict_extras": True}
    # One member of a stored hangar group: valid because the rest comes from the stored values.
    resp = client.post("/api/doctrine/settings", json={"strategic_reserve_hangar_flag": "CorpSAG2"})
    assert resp.status_code == 200, resp.text
    assert _stored("doctrine")["strategic_reserve_hangar_flag"] == "CorpSAG2"
    assert _stored("doctrine")["strategic_reserve_owner_id"] == 1
    assert client.post("/api/doctrine/settings", json={"strategic_reserve_hangar_flag": "Nope"}).status_code == 400


def test_old_full_form_bodies_stay_valid_for_every_route():
    for path in ("/api/production/settings", "/api/station-trading/settings", "/api/refining/settings",
                 "/api/module-reprocessing/settings", "/api/doctrine/settings"):
        form = client.get(path).json()
        form = {k: v for k, v in form.items()
                if k not in ("effective_structure_ids", "effective_stockpile_location_ids", "contract_structures")}
        resp = client.post(path, json=form)
        assert resp.status_code == 200, (path, resp.text)


def test_boot_load_applies_all_seven_scopes(monkeypatch):
    targets = {
        "trading": (TRADING_CONFIG, "import_cost_per_m3", 1234.0),
        "production": (PRODUCTION_CONFIG, "haul_cost_per_m3", 777.0),
        "doctrine": (DOCTRINE_CONFIG, "cargo_tolerance_pct", 12.5),
        "pi": (PI_CONFIG, "hub_region_id", 10000043),
        "refining": (REFINING_CONFIG, "refining_tax_rate", 0.07),
        "station_trading": (STATION_TRADING_CONFIG, "broker_fee_rate", 0.033),
        "module_reprocessing": (MODULE_REPROCESSING_CONFIG, "freight_cost_per_m3", 555.0),
    }
    assert set(targets) == set(SCOPES)
    for scope, (cfg, field, value) in targets.items():
        monkeypatch.setattr(cfg, field, getattr(cfg, field))  # restored on teardown
        _seed(scope, {field: value}, live=False)
    app_module._load_default_tenant_config()
    for scope, (cfg, field, value) in targets.items():
        assert getattr(cfg, field) == value, scope

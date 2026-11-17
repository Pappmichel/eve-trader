"""Industry skill levels and the buy broker fee are editable through the
settings routes (audit package M, fix 1)."""
import pytest
from fastapi.testclient import TestClient

from eve_trader import scheduler, storage, tenant_scope
from eve_trader.api.app import create_app
from eve_trader.production import engine as production_engine

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema  # noqa: F401

pytestmark = pg_helpers.postgres_required()

client = TestClient(create_app())
DEFAULT = storage.DEFAULT_TENANT_ID

SKILLS = ("industry_skill_level", "advanced_industry_skill_level", "reactions_skill_level",
          "specialist_skill_level")


@pytest.fixture(autouse=True)
def _default_tenant_settings(monkeypatch):
    monkeypatch.setattr(scheduler, "start", lambda: None)
    pg_helpers.wipe_tables("tenant_settings")
    with tenant_scope.enter_tenant(DEFAULT):
        yield
    pg_helpers.wipe_tables("tenant_settings")


def _stored(scope):
    with storage.tenant_context(DEFAULT):
        return storage.load_tenant_settings(scope)


def test_production_skill_levels_roundtrip():
    body = client.get("/api/production/settings").json()
    assert all(k in body for k in SKILLS)
    resp = client.post("/api/production/settings", json={"industry_skill_level": 4, "specialist_skill_level": 3})
    assert resp.status_code == 200, resp.text
    assert _stored("production") == {"industry_skill_level": 4, "specialist_skill_level": 3}
    after = client.get("/api/production/settings").json()
    assert after["industry_skill_level"] == 4 and after["specialist_skill_level"] == 3
    client.post("/api/production/settings", json={"industry_skill_level": 5, "specialist_skill_level": 5})


@pytest.mark.parametrize("field", SKILLS)
def test_production_skill_level_above_five_is_rejected(field):
    resp = client.post("/api/production/settings", json={field: 6})
    assert resp.status_code == 400
    assert field not in _stored("production")


def test_trading_buy_broker_fee_roundtrip_and_cache_invalidation(monkeypatch):
    calls = []
    monkeypatch.setattr(production_engine, "invalidate_discover_cache",
                        lambda *a, **k: calls.append(1))
    assert "jita_buy_broker_fee" in client.get("/api/trading/settings").json()
    resp = client.post("/api/trading/settings", json={"jita_buy_broker_fee": 0.02})
    assert resp.status_code == 200, resp.text
    assert _stored("trading") == {"jita_buy_broker_fee": 0.02}
    assert client.get("/api/trading/settings").json()["jita_buy_broker_fee"] == 0.02
    assert calls == [1]
    # An unrelated save leaves the production cache alone.
    assert client.post("/api/trading/settings", json={"min_paired_days": 6}).status_code == 200
    assert calls == [1]
    client.post("/api/trading/settings", json={"jita_buy_broker_fee": 0.0147})


def test_trading_buy_broker_fee_above_one_is_rejected():
    resp = client.post("/api/trading/settings", json={"jita_buy_broker_fee": 1.5})
    assert resp.status_code == 400
    assert "jita_buy_broker_fee" not in _stored("trading")

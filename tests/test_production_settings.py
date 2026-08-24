import pytest

from eve_trader.esi_client import ESIClient
from eve_trader.production import actions
from eve_trader.production.actions import ActionError
from eve_trader.production.config import ProductionConfig


def test_do_set_system_no_longer_calls_esi(monkeypatch):
    # do_set_system used to re-resolve system_name via a live
    # ESIClient().resolve_system_id() call on every save - now the frontend
    # already has a real system_id from the static local SDE system list,
    # so this must succeed even if resolve_system_id would fail/not exist.
    def _fail(*args, **kwargs):
        pytest.fail("do_set_system must not call ESI at all")
    monkeypatch.setattr(ESIClient, "resolve_system_id", _fail)
    monkeypatch.setattr(actions, "save_tenant_config_overrides", lambda *a, **k: None)
    monkeypatch.setattr(actions, "invalidate_discover_cache", lambda: None)
    monkeypatch.setattr(actions, "invalidate_ship_margin_cache", lambda: None)

    result = actions.do_set_system("component", 30000142, "Jita", cfg=ProductionConfig())

    assert result == {"component_system_name": "Jita", "component_system_id": 30000142}


def test_do_set_system_persists_id_and_name(monkeypatch):
    captured = {}
    monkeypatch.setattr(actions, "save_tenant_config_overrides",
                         lambda scope, updates, cfg, cfg_type: captured.update(updates))
    monkeypatch.setattr(actions, "invalidate_discover_cache", lambda: None)
    monkeypatch.setattr(actions, "invalidate_ship_margin_cache", lambda: None)

    actions.do_set_system("manufacturing", 30002187, "Amarr", cfg=ProductionConfig())

    assert captured == {"manufacturing_system_id": 30002187, "manufacturing_system_name": "Amarr"}


def test_do_set_system_rejects_unknown_profile():
    with pytest.raises(ActionError, match="Unknown profile"):
        actions.do_set_system("bogus", 30000142, "Jita")

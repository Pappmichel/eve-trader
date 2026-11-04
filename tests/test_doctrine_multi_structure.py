"""Several Doctrine contract structures and stockpile locations
(user request 2026-11-04): config fallbacks, the contracts fetcher filter,
and the actions' location filters / settings round trip."""
from __future__ import annotations

import pytest

from eve_trader.actions import ActionError
from eve_trader.config import ConfigError, TRADING_CONFIG
from eve_trader.doctrine import actions, engine
from eve_trader.doctrine.config import DoctrineConfig, fetcher_extra, validate_doctrine_overrides
from eve_trader.esi_data import fetchers

A, B, C = 1000000000001, 1000000000002, 1000000000003


@pytest.fixture(autouse=True)
def _no_trading_structure(monkeypatch):
    monkeypatch.setattr(TRADING_CONFIG, "structure_id", None)


@pytest.fixture(autouse=True)
def _no_location_names(monkeypatch):
    from eve_trader.locations import reader
    monkeypatch.setattr(reader, "describe", lambda ids: {})


# ---------------------------------------------------------------- config
def test_list_wins_over_legacy_ids():
    cfg = DoctrineConfig(doctrine_structure_id=C, doctrine_structure_ids=(A, B, A),
                         stockpile_location_id=C, stockpile_location_ids=(B,))
    assert cfg.effective_structure_ids == (A, B)
    assert cfg.effective_stockpile_location_ids == (B,)


def test_legacy_single_ids_still_apply():
    cfg = DoctrineConfig(doctrine_structure_id=A, stockpile_location_id=B)
    assert cfg.effective_structure_ids == (A,)
    assert cfg.effective_stockpile_location_ids == (B,)


def test_stockpile_falls_back_to_contract_structures_then_trading(monkeypatch):
    assert DoctrineConfig(doctrine_structure_ids=(A, B)).effective_stockpile_location_ids == (A, B)
    assert DoctrineConfig().effective_structure_ids == ()
    assert fetcher_extra(DoctrineConfig()) is None
    monkeypatch.setattr(TRADING_CONFIG, "structure_id", C)
    assert DoctrineConfig().effective_stockpile_location_ids == (C,)
    assert fetcher_extra(DoctrineConfig()) == {"structure_ids": [C]}


def test_invalid_location_ids_are_rejected():
    with pytest.raises(ConfigError):
        validate_doctrine_overrides({"doctrine_structure_ids": [A, -1]})
    with pytest.raises(ConfigError):
        validate_doctrine_overrides({"stockpile_location_ids": [True]})


# ---------------------------------------------------------------- fetcher filter
def test_contract_filter_keeps_every_configured_structure():
    contracts = [{"contract_id": i, "start_location_id": loc} for i, loc in enumerate((A, B, C))]
    kept = fetchers._at_structure(contracts, fetchers._structure_filter({"structure_ids": [A, B]}))
    assert [c["start_location_id"] for c in kept] == [A, B]
    # Legacy single id still filters; no extra means no filter.
    assert len(fetchers._at_structure(contracts, fetchers._structure_filter({"structure_id": C}))) == 1
    assert fetchers._at_structure(contracts, fetchers._structure_filter({})) == contracts


# ---------------------------------------------------------------- actions
def test_stockpile_filter_must_be_a_stockpile_location(monkeypatch):
    cfg = DoctrineConfig(stockpile_location_ids=(A, B))
    seen = {}
    monkeypatch.setattr(actions, "match_pending_contracts", lambda cfg=None: 0)
    monkeypatch.setattr(engine, "stockpile_rows_for_doctrine",
                        lambda doctrine_id, cfg, location_id=None: seen.update(loc=location_id) or ([], True))

    result = actions.do_get_stockpile_status(location_id=B, cfg=cfg)
    assert seen["loc"] == B
    assert [o["location_id"] for o in result["locations"]] == [A, B]
    with pytest.raises(ActionError):
        actions.do_get_stockpile_status(location_id=C, cfg=cfg)


def test_list_contracts_filters_by_structure(monkeypatch):
    from eve_trader import storage

    def _row(cid, loc):
        return (cid, "doctrine:1", False, 1, loc, "outstanding", "t", 1.0, None, None, None, "unmatched", None)
    monkeypatch.setattr(actions, "match_pending_contracts", lambda cfg=None: 0)
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [_row(1, A), _row(2, B)])
    monkeypatch.setattr(storage, "list_active_fittings", lambda: [])
    monkeypatch.setattr(actions, "_character_names_by_role_key", lambda: {})

    rows = actions.do_list_contracts(location_id=B)["rows"]
    assert [r["contract_id"] for r in rows] == [2]
    assert "start_location_name" in rows[0]


def test_settings_save_clears_legacy_ids(monkeypatch):
    saved = {}
    monkeypatch.setattr(actions, "save_tenant_config_overrides",
                        lambda scope, updates, cfg, cfg_type: saved.update(updates))
    actions.do_update_settings({"doctrine_structure_ids": [A, A, B], "stockpile_location_ids": []},
                               cfg=DoctrineConfig())
    assert saved["doctrine_structure_ids"] == [A, B]
    assert saved["doctrine_structure_id"] is None
    assert saved["stockpile_location_ids"] == [] and saved["stockpile_location_id"] is None


def test_get_settings_shows_legacy_ids_as_list():
    view = actions.do_get_settings(DoctrineConfig(doctrine_structure_id=A))
    assert view["doctrine_structure_ids"] == [A]
    assert view["stockpile_location_ids"] == []
    assert view["effective_stockpile_location_ids"] == [A]

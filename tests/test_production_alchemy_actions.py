from dataclasses import asdict

import pytest

from eve_trader import storage
from eve_trader.production import actions
from eve_trader.production.actions import ActionError
from eve_trader.production.config import ProductionConfig
from eve_trader.production.models import AlchemyComparison


def test_do_compare_alchemy_raises_on_unresolvable_name(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [])
    with pytest.raises(ActionError, match="No exact SDE match"):
        actions.do_compare_alchemy("Not A Real Item")


def test_do_compare_alchemy_raises_on_substring_only_match(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types",
                         lambda name, limit=5: [(1, "Unrefined Caesarium Cadmide")])
    with pytest.raises(ActionError, match="No exact SDE match"):
        actions.do_compare_alchemy("Caesarium")


def test_do_compare_alchemy_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types",
                         lambda name, limit=5: [(16675, "Caesarium Cadmide")])
    # Real compare_alchemy_profitability no-ops when the flag is off - do not
    # mock it, so this also proves the action does not treat "disabled" as an error.
    assert actions.do_compare_alchemy(
        "Caesarium Cadmide", ProductionConfig(alchemy_reactions_enabled=False),
    ) is None


def test_do_compare_alchemy_returns_none_when_no_pair(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(1, "Rifter")])
    monkeypatch.setattr(actions, "compare_alchemy_profitability", lambda *a, **k: None)
    assert actions.do_compare_alchemy(
        "Rifter", ProductionConfig(alchemy_reactions_enabled=True),
    ) is None


def test_do_compare_alchemy_returns_asdict_when_comparison_exists(monkeypatch):
    fake = AlchemyComparison(
        product_type_id=16675, product_type_name="Caesarium Cadmide",
        normal_isk_per_hour=200000.0, alchemy_isk_per_hour=4166.67,
        alchemy_unrefined_type_id=32824,
        alchemy_unrefined_type_name="Unrefined Caesarium Cadmide",
        scrapmetal_yield_pct=0.55,
    )
    monkeypatch.setattr(storage, "search_sde_types",
                         lambda name, limit=5: [(16675, "Caesarium Cadmide")])
    monkeypatch.setattr(actions, "compare_alchemy_profitability", lambda *a, **k: fake)
    assert actions.do_compare_alchemy("Caesarium Cadmide") == asdict(fake)

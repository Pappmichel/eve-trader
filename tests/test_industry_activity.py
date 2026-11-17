"""Live reaction activity id 9 is the SDE's 11 everywhere a live id meets SDE data."""
import pytest

from eve_trader.industry_activity import normalize_activity_id
from eve_trader.production.constants import ACTIVITY_REACTION
from eve_trader.realized import inputs, loader


@pytest.mark.parametrize("raw,expected", [(9, 11), (11, 11), (1, 1), (5, 5), (8, 8), ("9", 11)])
def test_normalize_activity_id(raw, expected):
    assert normalize_activity_id(raw) == expected


def test_helper_agrees_with_production_constant():
    assert normalize_activity_id(9) == ACTIVITY_REACTION


def test_loader_output_quantity_treats_9_as_reaction(monkeypatch):
    seen = []
    monkeypatch.setattr(loader.storage, "get_product_quantity",
                        lambda bp, act, product: seen.append(act) or 10)
    row = ("character", 1, 7, 9, None, 100, 200, 2, None, None)
    assert loader._output_quantity(row) == 20.0
    assert seen == [ACTIVITY_REACTION]


def test_loader_uses_bpc_treats_9_as_reaction():
    row = ("character", 1, 7, 9, 55, 100, 200, 2)
    assert loader._uses_bpc(row, {55: 3}, frozenset()) is True
    assert loader._uses_bpc(row, {55: -1}, frozenset()) is False


def test_freeze_job_inputs_looks_up_reaction_recipe_for_9(monkeypatch):
    seen = []
    monkeypatch.setattr(inputs.storage, "get_blueprint_materials",
                        lambda bp, act: seen.append(act) or [])
    out = inputs.freeze_job_inputs(
        owner_type="character", owner_id=1,
        job={"activity_id": 9, "blueprint_type_id": 100, "runs": 1},
        cfg=object(), selected_decryptors={},
    )
    assert out == [] and seen == [ACTIVITY_REACTION]

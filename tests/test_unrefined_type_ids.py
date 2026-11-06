"""storage.unrefined_type_ids: one cached query for alchemy "Unrefined *"
lookups, cleared on SDE apply. Alchemy stubs that used to mock
search_sde_types for the Unrefined counterpart now mock this instead."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.production.engine import find_alchemy_alternative

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_CAESARIUM = 16675
_UNREFINED = 32824
_NORMAL_BP = 46166
_ALCHEMY_BP = 46190


def _type_row(type_id: int, name: str, published: int = 1):
    return (type_id, 1, name, 0.01, published, None, None, None, 1)


@pytest.fixture(autouse=True)
def _clear_unrefined_cache():
    storage.unrefined_type_ids.cache_clear()
    yield
    storage.unrefined_type_ids.cache_clear()


def test_unrefined_type_ids_is_case_insensitive_and_keeps_lower_id_on_clash(tenant):
    storage.replace_sde_data(
        types=[
            _type_row(100, "Unrefined Alpha"),
            _type_row(50, "unrefined alpha"),  # same name, lower id
            _type_row(200, "Something Else"),
            _type_row(300, "Unrefined Draft", published=0),
        ],
        groups=[], market_groups=[], blueprint_time=[],
        blueprint_materials=[], blueprint_products=[],
    )
    mapping = storage.unrefined_type_ids()
    assert mapping["unrefined alpha"] == 50
    assert "unrefined draft" not in mapping
    assert "something else" not in mapping


def test_find_alchemy_alternative_hits_unrefined_cache_once(tenant, monkeypatch):
    storage.replace_sde_data(
        types=[
            _type_row(_CAESARIUM, "Caesarium Cadmide"),
            _type_row(_UNREFINED, "Unrefined Caesarium Cadmide"),
            _type_row(_NORMAL_BP, "Caesarium Cadmide Reaction Formula"),
            _type_row(_ALCHEMY_BP, "Unrefined Caesarium Cadmide Reaction Formula"),
        ],
        groups=[], market_groups=[], blueprint_time=[],
        blueprint_materials=[],
        blueprint_products=[
            (_NORMAL_BP, 11, _CAESARIUM, 200.0),
            (_ALCHEMY_BP, 11, _UNREFINED, 1.0),
        ],
        type_materials=[(_UNREFINED, 16643, 164.0), (_UNREFINED, _CAESARIUM, 36.0)],
    )
    # Real SDE readers for the product/blueprint side; only the Unrefined
    # name lookup must go through the cached helper under test.
    storage.get_blueprint_for_product.cache_clear()
    storage.get_sde_type.cache_clear()
    storage.get_type_materials.cache_clear()
    storage.unrefined_type_ids.cache_clear()

    before = storage.unrefined_type_ids.cache_info()
    assert find_alchemy_alternative(_CAESARIUM) is not None
    assert find_alchemy_alternative(_CAESARIUM) is not None
    after = storage.unrefined_type_ids.cache_info()
    assert after.misses == before.misses + 1
    assert after.hits == before.hits + 1


def test_unrefined_type_ids_reloads_after_sde_apply(tenant):
    storage.replace_sde_data(
        types=[_type_row(10, "Unrefined First")],
        groups=[], market_groups=[], blueprint_time=[],
        blueprint_materials=[], blueprint_products=[],
    )
    assert storage.unrefined_type_ids() == {"unrefined first": 10}

    storage.replace_sde_data(
        types=[_type_row(20, "Unrefined Second")],
        groups=[], market_groups=[], blueprint_time=[],
        blueprint_materials=[], blueprint_products=[],
    )
    assert storage.unrefined_type_ids() == {"unrefined second": 20}

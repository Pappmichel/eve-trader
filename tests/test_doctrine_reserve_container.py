"""A strategic reserve narrowed to one container: that container and everything
nested in it is the reserve, the rest of the division stays visible."""
import pytest

from eve_trader import storage
from eve_trader.config import ConfigError
from eve_trader.doctrine import actions, engine, reserve
from eve_trader.doctrine.config import DoctrineConfig, validate_doctrine_overrides
from eve_trader.doctrine.engine import _Candidate
from eve_trader.doctrine.models import Fitting, FittingItem

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

pytest.importorskip("psycopg")

CORP, OTHER_CORP, STRUCTURE = 98000001, 98000002, 1049588174021
OFFICE, CONTAINER, INNER, OTHER_CONTAINER = 700, 701, 702, 703
CONTAINER_TYPE, MODULE, HULL = 3293, 2048, 587
RESERVE_CFG = dict(strategic_reserve_owner_type="corporation", strategic_reserve_owner_id=CORP,
                   strategic_reserve_location_id=STRUCTURE, strategic_reserve_hangar_flag="CorpSAG5",
                   strategic_reserve_container_id=CONTAINER)


@pytest.fixture(autouse=True)
def _reset_cache():
    reserve.invalidate_cache()
    yield
    reserve.invalidate_cache()


def _insert(conn, item_id, type_id, location, flag, qty=1, owner=CORP):
    conn.execute(
        "INSERT INTO corp_assets (item_id, type_id, location_id, location_flag, quantity, is_blueprint_copy, "
        "owner_name, resolved_location_id, resolved_hangar_flag, owner_corporation_id, assembled_or_fitted) "
        "VALUES (?, ?, ?, ?, ?, 0, 'Corp', ?, 'CorpSAG5', ?, FALSE)",
        (item_id, type_id, location, flag, qty, STRUCTURE, owner))


def _seed(conn):
    _insert(conn, CONTAINER, CONTAINER_TYPE, OFFICE, "CorpSAG5")
    _insert(conn, INNER, CONTAINER_TYPE, CONTAINER, "Unlocked")
    _insert(conn, 1, MODULE, CONTAINER, "Unlocked", qty=100)          # in the container
    _insert(conn, 2, MODULE, INNER, "Unlocked", qty=10)               # nested one level deeper
    _insert(conn, OTHER_CONTAINER, CONTAINER_TYPE, OFFICE, "CorpSAG5")
    _insert(conn, 3, MODULE, OTHER_CONTAINER, "Unlocked", qty=7)      # another container, same division
    _insert(conn, 4, MODULE, OFFICE, "CorpSAG5", qty=5)               # loose in the same division


@pytest.mark.usefixtures("_wipe")
class TestStock:
    pytestmark = pg_helpers.postgres_required()

    def test_subtree_covers_nested_containers(self, tenant):
        with storage.connect() as conn:
            _seed(conn)
        assert storage.asset_subtree_item_ids("corporation", CORP, CONTAINER) == {CONTAINER, INNER, 1, 2}
        assert storage.asset_subtree_item_ids("corporation", CORP, 999) == frozenset()
        assert storage.asset_subtree_item_ids("corporation", OTHER_CORP, CONTAINER) == frozenset()

    def test_only_the_container_disappears(self, tenant):
        with storage.connect() as conn:
            _seed(conn)
        hangar = reserve.configured_hangar(DoctrineConfig(**RESERVE_CFG))
        assert storage.esi_stock_at_location(MODULE, None) == 122
        assert storage.esi_stock_at_location(MODULE, None, exclude_hangar=hangar) == 12
        assert storage.esi_stock_by_location_bulk([MODULE], exclude_hangar=hangar)[MODULE] == {STRUCTURE: 12}

    def test_a_missing_container_hides_nothing(self, tenant):
        with storage.connect() as conn:
            _seed(conn)
        cfg = DoctrineConfig(**{**RESERVE_CFG, "strategic_reserve_container_id": 999})
        hangar = reserve.configured_hangar(cfg)
        assert hangar.item_ids == frozenset()
        assert storage.esi_stock_at_location(MODULE, None, exclude_hangar=hangar) == 122

    def test_blueprints_in_the_container_disappear(self, tenant):
        with storage.connect() as conn:
            _seed(conn)
            for item_id, parent in ((20, CONTAINER), (21, OTHER_CONTAINER)):
                _insert(conn, item_id, 999, parent, "Unlocked")
                conn.execute(
                    "INSERT INTO corp_blueprints (item_id, type_id, location_id, location_flag, quantity, "
                    "material_efficiency, time_efficiency, runs, resolved_location_id, owner_corporation_id) "
                    "VALUES (?, 999, ?, 'Unlocked', -2, 1, 0, 30, ?, ?)", (item_id, parent, STRUCTURE, CORP))
        hangar = reserve.configured_hangar(DoctrineConfig(**RESERVE_CFG))
        assert storage.available_blueprint_copies(999, None) == 60
        assert storage.available_blueprint_copies(999, None, exclude_hangar=hangar) == 30

    def test_valuation_read_still_sees_it(self, tenant):
        with storage.connect() as conn:
            _seed(conn)
        assert storage.esi_stock_at_location_bulk([MODULE], location_id=None)[MODULE] == 122


@pytest.fixture
def _wipe():
    pg_helpers.wipe_tables("character_assets", "corp_assets", "character_blueprints", "corp_blueprints")
    yield


def test_cached_subtree_is_read_once_and_cleared_on_save(monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "t1")
    monkeypatch.setattr(storage, "asset_subtree_item_ids",
                        lambda *a: calls.append(a) or frozenset({CONTAINER}))
    cfg = DoctrineConfig(**RESERVE_CFG)
    reserve.configured_hangar(cfg)
    reserve.configured_hangar(cfg)
    assert len(calls) == 1
    reserve.invalidate_cache()
    reserve.configured_hangar(cfg)
    assert len(calls) == 2


def test_is_in_hangar_matches_by_item_id_and_owner():
    hangar = storage.ExcludedHangar("corporation", CORP, STRUCTURE, "CorpSAG5", CONTAINER, frozenset({CONTAINER, 1}))
    inside = {"item_id": 1, "resolved_location_id": STRUCTURE, "resolved_hangar_flag": "CorpSAG5"}
    loose = {"item_id": 4, "resolved_location_id": STRUCTURE, "resolved_hangar_flag": "CorpSAG5"}
    assert storage.is_in_hangar(inside, "corporation", CORP, hangar)
    assert not storage.is_in_hangar(loose, "corporation", CORP, hangar)
    assert not storage.is_in_hangar(inside, "corporation", OTHER_CORP, hangar)
    # Live paths compare a flag: a container reserve has none, so the division stays counted.
    assert storage.hangar_flag_for(hangar, "corporation", CORP, STRUCTURE) is None


def test_a_container_without_its_hangar_is_rejected():
    with pytest.raises(ConfigError):
        validate_doctrine_overrides({"strategic_reserve_owner_type": None, "strategic_reserve_owner_id": None,
                                     "strategic_reserve_location_id": None, "strategic_reserve_hangar_flag": None,
                                     "strategic_reserve_container_id": CONTAINER})
    validate_doctrine_overrides(RESERVE_CFG)


def _option(*containers):
    return [{"owner_type": "corporation", "owner_id": CORP, "owner_name": "Corp", "location_id": STRUCTURE,
             "hangar_flag": "CorpSAG5", "containers": [{"item_id": c} for c in containers]}]


def _reject_reserve_container(updates, cfg):
    actions._reject_foreign_container(
        updates, cfg, owner_type_key="strategic_reserve_owner_type",
        owner_id_key="strategic_reserve_owner_id", location_key="strategic_reserve_location_id",
        flag_key="strategic_reserve_hangar_flag", container_key="strategic_reserve_container_id")


def test_a_new_container_must_lie_in_the_chosen_hangar(monkeypatch):
    monkeypatch.setattr(reserve, "hangar_options", lambda: _option(OTHER_CONTAINER))
    with pytest.raises(actions.ActionError):
        _reject_reserve_container(RESERVE_CFG, DoctrineConfig())
    monkeypatch.setattr(reserve, "hangar_options", lambda: _option(CONTAINER))
    _reject_reserve_container(RESERVE_CFG, DoctrineConfig())


def test_an_unchanged_container_that_is_gone_stays_saveable(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "t1")
    monkeypatch.setattr(reserve, "hangar_options", lambda: _option())
    _reject_reserve_container(RESERVE_CFG, DoctrineConfig(**RESERVE_CFG))


def test_a_container_change_invalidates_the_cached_plans():
    assert actions._reserve_changed({"strategic_reserve_container_id": OTHER_CONTAINER},
                                    DoctrineConfig(**RESERVE_CFG))


# ------------------------------------------------------------------ doctrine rows
def _asset(item_id, type_id, qty, location, flag="Unlocked", **extra):
    return {"item_id": item_id, "type_id": type_id, "quantity": qty, "owner_type": "corporation", "owner_id": CORP,
            "owner_name": "Corp", "location_id": location, "location_flag": flag, "resolved_location_id": STRUCTURE,
            "resolved_hangar_flag": "CorpSAG5", "assembled_or_fitted": False, "is_singleton": False, **extra}


ROWS = [
    _asset(CONTAINER, CONTAINER_TYPE, 1, OFFICE, "CorpSAG5", is_singleton=True),
    _asset(INNER, CONTAINER_TYPE, 1, CONTAINER, is_singleton=True),
    _asset(1, MODULE, 4, CONTAINER),
    _asset(2, HULL, 1, INNER),
    _asset(4, MODULE, 6, OFFICE, "CorpSAG5"),   # same division, outside the container
    _asset(5, HULL, 3, OFFICE, "CorpSAG5"),
]


@pytest.fixture
def _doctrine(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: None)
    monkeypatch.setattr(storage, "list_doctrines", lambda: [("d1", "Doctrine")])
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [])
    monkeypatch.setattr(storage, "load_sorting_intake_sources", lambda: [])
    monkeypatch.setattr(storage, "get_sde_types_bulk",
                        lambda ids: {t: (t, 12 if t == CONTAINER_TYPE else 1, f"T{t}", 5.0, 1, 1, 0, None) for t in ids})
    monkeypatch.setattr(storage, "get_location_names", lambda ids: {i: f"S{i}" for i in ids})
    monkeypatch.setattr(storage, "load_asset_item_names", lambda table, owner: {CONTAINER: "Fleet Reserve"})
    monkeypatch.setattr(storage, "asset_subtree_item_ids", lambda *a: frozenset({CONTAINER, INNER, 1, 2}))
    monkeypatch.setattr(engine.equivalents, "lookup", lambda: (lambda t: (t,)))


def _candidate(target=0, stockpile=0):
    items = [FittingItem("f1", 1, "low", MODULE, 2)]
    fitting = Fitting(fitting_id="f1", doctrine_id="d1", name="Fit", hull_type_id=HULL, raw_eft="",
                      stockpile_target=stockpile, reserve_target=target)
    exact, consume = engine.validation.build_contract_soll(items)
    return _Candidate(fitting=fitting, exact_soll=exact, consume_soll=consume, items=items)


def test_status_counts_only_the_container(monkeypatch, _doctrine):
    from eve_trader.esi_data import access

    monkeypatch.setattr(engine, "load_match_candidates", lambda: [_candidate(target=1)])
    monkeypatch.setattr(access, "read_esi", lambda *a, **k: ROWS)
    monkeypatch.setattr(access, "is_shared", lambda *a, **k: True)
    result = reserve.status(DoctrineConfig(**RESERVE_CFG))
    assert result["state"] == "ok"
    assert result["hangar"]["container_name"] == "Fleet Reserve"
    assert result["fittings"][0]["complete_sets"] == 1  # 4 modules and 1 hull inside; the 6 and 3 outside stay out


def test_status_reports_a_missing_container(monkeypatch, _doctrine):
    from eve_trader.esi_data import access

    monkeypatch.setattr(storage, "asset_subtree_item_ids", lambda *a: frozenset())
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [_candidate(target=1)])
    monkeypatch.setattr(access, "read_esi", lambda *a, **k: ROWS)
    monkeypatch.setattr(access, "is_shared", lambda *a, **k: True)
    result = reserve.status(DoctrineConfig(**RESERVE_CFG))
    assert result["state"] == "container_missing"
    assert result["fittings"][0]["complete_sets"] is None


def test_stockpile_keeps_the_rest_of_the_division(monkeypatch, _doctrine):
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [_candidate(stockpile=4)])
    monkeypatch.setattr(engine, "read_esi", lambda *a, **k: ROWS)
    cfg = DoctrineConfig(stockpile_location_ids=(STRUCTURE,), **RESERVE_CFG)

    rows, _ = engine.stockpile_rows_for_doctrine(cfg=cfg)

    module = next(r for r in rows if r.type_id == MODULE)
    assert module.available == 6         # outside the container, still stockpile
    assert module.shortfall == 2
    assert module.reserve_on_hand == 2   # the container holds 4, capped at the shortfall


def test_hangar_options_list_containers_with_their_contents(monkeypatch, _doctrine):
    from eve_trader.esi_data import access

    monkeypatch.setattr(access, "read_esi", lambda *a, **k: ROWS)
    monkeypatch.setattr(storage, "load_corp_divisions", lambda ids: [])
    option = reserve.hangar_options()[0]
    by_id = {c["item_id"]: c for c in option["containers"]}
    assert set(by_id) == {CONTAINER, INNER}
    assert by_id[CONTAINER]["item_rows"] == 3      # the inner container, its hull and the module
    assert by_id[CONTAINER]["item_name"] == "Fleet Reserve"
    assert by_id[INNER]["item_rows"] == 1

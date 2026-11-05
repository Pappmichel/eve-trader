"""Strategic reserve (GitHub issue #418): one hangar whose contents do not exist
for Production and Trading, are not Doctrine stockpile, and still exist for
everyone who only asks what the tenant owns."""
import pytest

from eve_trader import own_orders, storage
from eve_trader.asset_manager import labels
from eve_trader.config import ConfigError
from eve_trader.doctrine import buyer_sources, engine, reserve, validation as v
from eve_trader.doctrine.config import DoctrineConfig, validate_doctrine_overrides
from eve_trader.doctrine.engine import _Candidate
from eve_trader.doctrine.models import Fitting, FittingItem
from eve_trader.production import actions as production_actions

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

pytest.importorskip("psycopg")

CORP, OTHER_CORP, PILOT = 98000001, 98000002, 90000001
STRUCTURE, OTHER_STRUCTURE = 1049588174021, 1049865172501
HULL, MODULE, SHARED, TWIN = 587, 2048, 2281, 2282
HANGAR = storage.ExcludedHangar("corporation", CORP, STRUCTURE, "CorpSAG5")
RESERVE_CFG = dict(strategic_reserve_owner_type="corporation", strategic_reserve_owner_id=CORP,
                   strategic_reserve_location_id=STRUCTURE, strategic_reserve_hangar_flag="CorpSAG5")


@pytest.fixture(autouse=True)
def _reset_cache():
    reserve.invalidate_cache()
    yield
    reserve.invalidate_cache()


@pytest.fixture
def _wipe():
    pg_helpers.wipe_tables("character_assets", "corp_assets", "character_blueprints", "corp_blueprints")
    yield


def _corp_row(conn, item_id, type_id, flag, *, owner=CORP, location=STRUCTURE, qty=10):
    conn.execute(
        "INSERT INTO corp_assets (item_id, type_id, location_id, location_flag, quantity, is_blueprint_copy, "
        "owner_name, resolved_location_id, resolved_hangar_flag, owner_corporation_id, assembled_or_fitted) "
        "VALUES (?, ?, ?, ?, ?, 0, 'Corp', ?, ?, ?, FALSE)",
        (item_id, type_id, location, flag or "Unlocked", qty, location, flag, owner))


# ----------------------------------------------------------------- stock reads
@pytest.mark.usefixtures("_wipe")
class TestStockReaders:
    pytestmark = pg_helpers.postgres_required()

    def _seed(self):
        with storage.connect() as conn:
            _corp_row(conn, 1, MODULE, "CorpSAG5", qty=100)                         # the reserve
            _corp_row(conn, 2, MODULE, "CorpSAG1", qty=20)                          # other division
            _corp_row(conn, 3, MODULE, "CorpSAG5", location=OTHER_STRUCTURE, qty=3)  # other structure
            _corp_row(conn, 4, MODULE, "CorpSAG5", owner=OTHER_CORP, qty=4)         # other owner
            _corp_row(conn, 5, MODULE, None, qty=5)                                 # synced before the column

    def test_only_the_exact_hangar_disappears(self, tenant):
        self._seed()
        assert storage.esi_stock_at_location(MODULE, None) == 132
        assert storage.esi_stock_at_location(MODULE, None, exclude_hangar=HANGAR) == 32
        assert storage.esi_stock_at_location_bulk([MODULE], STRUCTURE, exclude_hangar=HANGAR)[MODULE] == 29

    def test_by_location_reader_applies_it_too(self, tenant):
        self._seed()
        by_location = storage.esi_stock_by_location_bulk([MODULE], exclude_hangar=HANGAR)[MODULE]
        assert by_location == {STRUCTURE: 29, OTHER_STRUCTURE: 3}

    def test_a_character_hangar_only_touches_that_character(self, tenant):
        with storage.connect() as conn:
            for item_id, owner, qty in ((1, PILOT, 7), (2, PILOT + 1, 9)):
                conn.execute(
                    "INSERT INTO character_assets (item_id, type_id, location_id, location_flag, quantity, "
                    "is_blueprint_copy, owner_name, resolved_location_id, resolved_hangar_flag, "
                    "owner_character_id, assembled_or_fitted) VALUES (?, ?, ?, 'Hangar', ?, 0, 'P', ?, 'Hangar', ?, FALSE)",
                    (item_id, MODULE, STRUCTURE, qty, STRUCTURE, owner))
        personal = storage.ExcludedHangar("character", PILOT, STRUCTURE, "Hangar")
        assert storage.esi_stock_at_location(MODULE, None, exclude_hangar=personal) == 9

    def test_blueprints_in_the_hangar_disappear(self, tenant):
        with storage.connect() as conn:
            _corp_row(conn, 10, 999, "CorpSAG5", qty=1)
            _corp_row(conn, 11, 999, "CorpSAG1", qty=1)
            for item_id, quantity, runs, me in ((10, -1, -1, 10), (11, -2, 40, 3)):
                conn.execute(
                    "INSERT INTO corp_blueprints (item_id, type_id, location_id, location_flag, quantity, "
                    "material_efficiency, time_efficiency, runs, resolved_location_id, owner_corporation_id) "
                    "VALUES (?, 999, ?, 'CorpSAG5', ?, ?, 0, ?, ?, ?)",
                    (item_id, STRUCTURE, quantity, me, runs, STRUCTURE, CORP))
            conn.execute(
                "INSERT INTO corp_blueprints (item_id, type_id, location_id, location_flag, quantity, "
                "material_efficiency, time_efficiency, runs, resolved_location_id, owner_corporation_id) "
                "VALUES (12, 999, ?, 'CorpSAG5', -2, 2, 0, 30, ?, ?)", (STRUCTURE, STRUCTURE, CORP))
            _corp_row(conn, 12, 999, "CorpSAG5", qty=1)

        assert storage.get_owned_bpo_best_me_te(999) == (10, 0)
        assert storage.get_owned_bpo_best_me_te(999, exclude_hangar=HANGAR) is None
        assert storage.has_bpo_at_location(999, STRUCTURE) is True
        assert storage.has_bpo_at_location(999, STRUCTURE, exclude_hangar=HANGAR) is False
        assert storage.available_blueprint_copies(999, None) == 70
        assert storage.available_blueprint_copies(999, None, exclude_hangar=HANGAR) == 40
        assert sorted(g[1] for g in storage.list_blueprint_copies(999, exclude_hangar=HANGAR)) == [40]
        assert storage.count_owned_formula_originals([999]) == {999: 1}
        assert storage.count_owned_formula_originals([999], exclude_hangar=HANGAR) == {}
        assert len(storage.load_owned_blueprints()) == 3
        assert len(storage.load_owned_blueprints(exclude_hangar=HANGAR)) == 1

    def test_valuation_readers_are_untouched(self, tenant):
        """Realized reads without the parameter, Portfolio has no such parameter."""
        self._seed()
        assert storage.esi_stock_at_location_bulk([MODULE], location_id=None)[MODULE] == 132
        grouped = storage.load_assets_grouped(None, [CORP])
        assert sum(row[-1] for row in grouped) == 128   # every row of this corp, the reserve included


# ------------------------------------------------------------------ live rows
def test_is_in_hangar_reads_snapshot_and_live_rows():
    snapshot = {"resolved_location_id": STRUCTURE, "resolved_hangar_flag": "CorpSAG5", "location_flag": "Unlocked",
                "location_id": 7}
    live = {"location_id": STRUCTURE, "location_flag": "CorpSAG5"}
    assert storage.is_in_hangar(snapshot, "corporation", CORP, HANGAR)
    assert storage.is_in_hangar(live, "corporation", CORP, HANGAR)
    assert not storage.is_in_hangar(snapshot, "corporation", OTHER_CORP, HANGAR)
    assert not storage.is_in_hangar(snapshot, "character", CORP, HANGAR)
    assert not storage.is_in_hangar(snapshot, "corporation", CORP, None)


def test_unlisted_stock_skips_the_reserve_division(monkeypatch):
    monkeypatch.setattr(storage, "mark_assembled_or_fitted",
                        lambda rows: [r.update(assembled_or_fitted=False) or r for r in rows] and rows)
    office = 555
    assets = [
        {"item_id": office, "type_id": storage.OFFICE_TYPE_ID, "location_id": STRUCTURE, "location_flag": "OfficeFolder"},
        {"item_id": 1, "type_id": MODULE, "location_id": office, "location_flag": "CorpSAG5", "quantity": 100},
        {"item_id": 2, "type_id": MODULE, "location_id": office, "location_flag": "CorpSAG1", "quantity": 20},
    ]
    out = {}
    production_actions._accumulate_stock_at_location(assets, STRUCTURE, out, skip_flag="CorpSAG5")
    assert out[MODULE] == 20
    assert storage.hangar_flag_for(HANGAR, "corporation", CORP, STRUCTURE) == "CorpSAG5"
    assert storage.hangar_flag_for(HANGAR, "corporation", CORP, OTHER_STRUCTURE) is None


def test_trading_ignores_a_personal_reserve_hangar(monkeypatch):
    personal = storage.ExcludedHangar("character", PILOT, STRUCTURE, "Hangar")
    monkeypatch.setattr(own_orders, "_reserve_hangar", lambda: personal)
    assets = [{"type_id": MODULE, "location_id": STRUCTURE, "location_flag": "Hangar", "quantity": 5,
               "assembled_or_fitted": False}]
    monkeypatch.setattr(own_orders, "_character_assets", lambda *a: assets)
    monkeypatch.setattr(own_orders, "fetch_own_sell_orders", lambda *a, **k: {})

    class Cfg:
        structure_id = STRUCTURE
        jita_region_id = 10000002

    class Client:
        def character_orders(self, *a, **k):
            return []

    monkeypatch.setattr(own_orders, "_hub_station_ids", lambda region: frozenset())
    assert own_orders.fetch_seller_stock_without_order_pooled([(PILOT, "r")], Client(), {MODULE}, Cfg) == []
    assert own_orders.fetch_buyer_already_covered(PILOT, "r", Client(), Cfg) == set()
    # Another character's hangar at the same structure still counts.
    other = own_orders.fetch_seller_stock_without_order_pooled([(PILOT + 1, "r")], Client(), {MODULE}, Cfg)
    assert [r["type_id"] for r in other] == [MODULE]
    assert own_orders.fetch_buyer_already_covered(PILOT + 1, "r", Client(), Cfg) == {MODULE}


# ------------------------------------------------------------------- the grant
def test_losing_the_doctrine_grant_makes_the_reserve_inert(monkeypatch):
    from eve_trader import tenant_eligibility

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "t1")
    monkeypatch.setattr(tenant_eligibility, "granted_tools", lambda t: {"production"})
    cfg = DoctrineConfig(**RESERVE_CFG)
    assert reserve.configured_hangar(cfg) == HANGAR
    assert reserve.active_hangar(cfg) is None

    reserve.invalidate_cache()
    monkeypatch.setattr(tenant_eligibility, "granted_tools", lambda t: {"doctrine"})
    assert reserve.active_hangar(cfg) == HANGAR


def test_no_reserve_set_needs_no_grant_lookup(monkeypatch):
    from eve_trader import tenant_eligibility

    def _boom(_tenant):
        raise AssertionError("a tenant without a reserve must not query grants")
    monkeypatch.setattr(tenant_eligibility, "granted_tools", _boom)
    assert reserve.active_hangar(DoctrineConfig()) is None


def test_asset_manager_does_not_count_the_reserve_as_stock():
    assert labels.is_stock("esi", []) is True
    assert labels.is_stock("esi", ["strategic_reserve"]) is False


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("overrides", [
    {"strategic_reserve_owner_type": "corporation"},
    {**RESERVE_CFG, "strategic_reserve_location_id": None},
    {**RESERVE_CFG, "strategic_reserve_owner_type": "alliance"},
    {**RESERVE_CFG, "strategic_reserve_hangar_flag": "Deliveries"},
])
def test_invalid_hangar_settings_are_rejected(overrides):
    with pytest.raises(ConfigError):
        validate_doctrine_overrides(overrides)


def test_a_complete_hangar_or_none_at_all_is_accepted():
    validate_doctrine_overrides(RESERVE_CFG)
    validate_doctrine_overrides({key: None for key in RESERVE_CFG})


# -------------------------------------------------------------------- doctrine
def _candidate(fitting_id="f1", name="Fit", items=None, hull=HULL, reserve_target=0, stockpile_target=0):
    items = items if items is not None else [FittingItem(fitting_id, 1, "low", MODULE, 2)]
    fitting = Fitting(fitting_id=fitting_id, doctrine_id="d1", name=name, hull_type_id=hull, raw_eft="",
                      stockpile_target=stockpile_target, reserve_target=reserve_target)
    exact, consume = v.build_contract_soll(items)
    return _Candidate(fitting=fitting, exact_soll=exact, consume_soll=consume, items=items)


def _asset(type_id, qty, *, flag="CorpSAG5", location=STRUCTURE, owner=CORP, **extra):
    return {"type_id": type_id, "quantity": qty, "owner_type": "corporation", "owner_id": owner, "owner_name": "Corp",
            "location_id": location, "location_flag": flag, "resolved_location_id": location,
            "resolved_hangar_flag": flag, "assembled_or_fitted": False, **extra}


@pytest.fixture
def _doctrine(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: None)
    monkeypatch.setattr(storage, "list_doctrines", lambda: [("d1", "Doctrine")])
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [])
    monkeypatch.setattr(storage, "load_sorting_intake_sources", lambda: [])
    monkeypatch.setattr(storage, "get_sde_types_bulk",
                        lambda ids: {t: (t, 1, f"T{t}", 5.0, 1, 1, 0, None) for t in ids})
    monkeypatch.setattr(storage, "get_location_names", lambda ids: {i: f"S{i}" for i in ids})
    monkeypatch.setattr(engine.equivalents, "lookup", lambda: (lambda t: (t,)))
    monkeypatch.setattr(engine, "classify_activity", lambda t: (None, None))


def test_stockpile_ignores_the_reserve_but_names_it_as_a_hint(monkeypatch, _doctrine):
    cand = _candidate(stockpile_target=2)
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [cand])
    monkeypatch.setattr(engine, "read_esi", lambda *a, **k: [
        _asset(MODULE, 100), _asset(MODULE, 1, flag="CorpSAG1"), _asset(HULL, 2, flag="CorpSAG1")])
    cfg = DoctrineConfig(stockpile_location_ids=(STRUCTURE,), **RESERVE_CFG)

    rows, _ = engine.stockpile_rows_for_doctrine(cfg=cfg)

    row = next(r for r in rows if r.type_id == MODULE)
    assert row.available == 1
    assert row.shortfall == 3            # the 100 in the reserve do not cover it
    assert row.severity is not None
    assert row.reserve_on_hand == 3      # capped at the shortfall
    assert row.reserve_pool == 100
    hull = next(r for r in rows if r.type_id == HULL)
    assert hull.shortfall == 0 and hull.reserve_on_hand == 0


def test_aggregated_hint_is_capped_by_the_physical_stock(monkeypatch, _doctrine):
    # Both fits show the same 5 units; the combined row must not say 10.
    first, second = _candidate("f1", stockpile_target=5), _candidate("f2", stockpile_target=5)
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [first, second])
    monkeypatch.setattr(engine, "read_esi", lambda *a, **k: [_asset(MODULE, 5)])
    cfg = DoctrineConfig(stockpile_location_ids=(STRUCTURE,), **RESERVE_CFG)

    rows, _ = engine.stockpile_rows_for_doctrine(cfg=cfg)
    combined = {r.type_id: r for r in engine.aggregate_stockpile_rows(rows)}[MODULE]

    assert [r.reserve_on_hand for r in rows if r.type_id == MODULE] == [5, 5]
    assert combined.shortfall == 20
    assert combined.reserve_on_hand == 5


def test_assemblable_does_not_count_the_reserve(monkeypatch, _doctrine):
    cand = _candidate()
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [cand])
    monkeypatch.setattr(engine, "load_fitting_with_items", lambda fid: (cand.fitting, cand.items))
    monkeypatch.setattr(storage, "get_sde_type", lambda t: (t, 1, f"T{t}", 5.0, 1, 1, 0, None))
    monkeypatch.setattr(engine, "read_esi", lambda *a, **k: [
        _asset(MODULE, 100), _asset(HULL, 50), _asset(MODULE, 4, flag="CorpSAG1"), _asset(HULL, 5, flag="CorpSAG1")])
    cfg = DoctrineConfig(stockpile_location_ids=(STRUCTURE,), **RESERVE_CFG)

    result = engine.assemblable_for_fitting("f1", cfg, target_sets=4)

    assert result["max_sets"] == 2       # 4 modules / 2 per fit, the reserve's 100 stay out
    rows = {r["type_id"]: r for r in result["requirements"]}
    assert rows[MODULE]["short_for_next"] == 2
    assert rows[MODULE]["short_for_target"] == 4
    assert rows[MODULE]["reserve_on_hand"] == 4
    assert rows[HULL]["reserve_on_hand"] == 0


def test_buyer_hangars_do_not_count_the_reserve(monkeypatch):
    personal = storage.ExcludedHangar("character", PILOT, STRUCTURE, "Hangar")
    monkeypatch.setattr(buyer_sources.reserve, "active_hangar", lambda: personal)
    row = {"type_id": MODULE, "quantity": 9, "owner_type": "character", "owner_id": PILOT, "location_id": STRUCTURE,
           "location_flag": "Hangar", "resolved_location_id": STRUCTURE, "resolved_hangar_flag": "Hangar",
           "assembled_or_fitted": False}
    monkeypatch.setattr(buyer_sources, "read_esi", lambda *a, **k: [row])
    monkeypatch.setattr(buyer_sources.reader, "describe", lambda ids: {})
    monkeypatch.setattr(storage, "load_sorting_intake_sources", lambda: [])
    assert buyer_sources.hub_hangar_stock([MODULE], {PILOT}, {1}, {STRUCTURE}, set()) == {MODULE: 0.0}


# ---------------------------------------------------------------- the reserve tab
def _status(monkeypatch, candidates, rows, **cfg):
    from eve_trader.esi_data import access

    monkeypatch.setattr(engine, "load_match_candidates", lambda: candidates)
    monkeypatch.setattr(access, "read_esi", lambda *a, **k: rows)
    monkeypatch.setattr(access, "is_shared", lambda *a, **k: True)
    return reserve.status(DoctrineConfig(**RESERVE_CFG, **cfg))


def test_status_without_a_hangar_lists_the_fittings_for_their_targets(monkeypatch, _doctrine):
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [_candidate(reserve_target=3)])
    result = reserve.status(DoctrineConfig())
    assert result["state"] == "not_configured"
    assert result["fittings"][0]["reserve_target"] == 3
    assert result["fittings"][0]["complete_sets"] is None


def test_status_reports_an_owner_that_does_not_share_assets(monkeypatch, _doctrine):
    from eve_trader.esi_data import access

    monkeypatch.setattr(engine, "load_match_candidates", lambda: [_candidate(reserve_target=1)])
    monkeypatch.setattr(access, "is_shared", lambda *a, **k: False)
    assert reserve.status(DoctrineConfig(**RESERVE_CFG))["state"] == "not_shared"


def test_status_counts_sets_and_warns_below_target(monkeypatch, _doctrine):
    result = _status(monkeypatch, [_candidate(reserve_target=3)], [_asset(MODULE, 4), _asset(HULL, 10)])
    assert result["state"] == "ok"
    row = result["fittings"][0]
    assert row["complete_sets"] == 2
    assert row["missing_units"] == 2
    assert result["below_target"] == [{"fitting_id": "f1", "fitting_name": "Fit", "complete_sets": 2, "reserve_target": 3}]
    assert [(m["type_id"], m["missing"]) for m in result["missing"]] == [(MODULE, 2)]


def test_status_no_warning_at_target(monkeypatch, _doctrine):
    result = _status(monkeypatch, [_candidate(reserve_target=2)], [_asset(MODULE, 4), _asset(HULL, 2)])
    assert result["below_target"] == []
    assert result["missing"] == []


def test_a_shared_module_is_not_counted_for_two_fits(monkeypatch, _doctrine):
    first = _candidate("f1", "First", [FittingItem("f1", 1, "low", SHARED, 1)], reserve_target=2)
    second = _candidate("f2", "Second", [FittingItem("f2", 1, "low", SHARED, 1)], hull=HULL + 1, reserve_target=2)
    rows = [_asset(SHARED, 2), _asset(HULL, 2), _asset(HULL + 1, 2)]

    result = _status(monkeypatch, [first, second], rows)

    sets = {f["fitting_name"]: f["complete_sets"] for f in result["fittings"]}
    assert sets == {"First": 2, "Second": 0}
    assert [m["missing"] for m in result["missing"]] == [2]


def test_hull_switch_off_drops_the_hull_from_the_requirement(monkeypatch, _doctrine):
    result = _status(monkeypatch, [_candidate(reserve_target=2)], [_asset(MODULE, 4)],
                     strategic_reserve_include_hulls=False)
    assert result["fittings"][0]["complete_sets"] == 2
    assert result["missing"] == []


def test_hull_switch_on_requires_the_hull(monkeypatch, _doctrine):
    result = _status(monkeypatch, [_candidate(reserve_target=2)], [_asset(MODULE, 4)])
    assert result["fittings"][0]["complete_sets"] == 0
    assert [(m["type_id"], m["missing"]) for m in result["missing"]] == [(HULL, 2)]


def test_assembled_ships_are_not_counted_but_reported(monkeypatch, _doctrine):
    rows = [_asset(HULL, 1, assembled_or_fitted=True, is_singleton=True),
            _asset(MODULE, 2, flag="LoSlot0", assembled_or_fitted=True) | {"resolved_hangar_flag": "CorpSAG5"},
            _asset(MODULE, 2)]
    result = _status(monkeypatch, [_candidate(reserve_target=1)], rows)
    assert result["assembled_ships_not_counted"] == 1   # the ship, not its fitted module
    assert result["fittings"][0]["complete_sets"] == 0  # the assembled hull is not stock
    assert [m["type_id"] for m in result["missing"]] == [HULL]


def test_status_counts_equivalent_variants(monkeypatch, _doctrine):
    monkeypatch.setattr(engine.equivalents, "lookup",
                        lambda: (lambda t: (SHARED, TWIN) if t in (SHARED, TWIN) else (t,)))
    cand = _candidate(items=[FittingItem("f1", 1, "low", SHARED, 1)], reserve_target=2)
    result = _status(monkeypatch, [cand], [_asset(TWIN, 2), _asset(HULL, 2)])
    assert result["fittings"][0]["complete_sets"] == 2


def test_hangar_options_list_only_real_hangar_divisions(monkeypatch):
    from eve_trader.esi_data import access

    monkeypatch.setattr(access, "read_esi", lambda *a, **k: [
        _asset(MODULE, 1), _asset(HULL, 1), _asset(MODULE, 1, flag="AssetSafety"),
        _asset(MODULE, 1, flag="CorpSAG1", location=OTHER_STRUCTURE)])
    monkeypatch.setattr(storage, "get_location_names", lambda ids: {STRUCTURE: "C-J"})
    monkeypatch.setattr(storage, "load_corp_divisions", lambda ids: [(CORP, "hangar", 5, "Reserve")])

    options = reserve.hangar_options()

    by_flag = {o["hangar_flag"]: o for o in options}
    assert set(by_flag) == {"CorpSAG1", "CorpSAG5"}
    assert by_flag["CorpSAG5"]["item_rows"] == 2
    assert by_flag["CorpSAG5"]["division_name"] == "Reserve"
    assert by_flag["CorpSAG5"]["location_name"] == "C-J"

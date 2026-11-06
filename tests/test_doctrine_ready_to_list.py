"""Ready to list (GitHub issue #429): one hangar of assembled ships counted
when their fit is valid or tolerable. Not an exclude hangar."""
import pytest

from eve_trader import storage
from eve_trader.config import ConfigError
from eve_trader.doctrine import actions, engine, ready_to_list, validation as v
from eve_trader.doctrine.config import DoctrineConfig, validate_doctrine_overrides
from eve_trader.doctrine.engine import _Candidate
from eve_trader.doctrine.models import Fitting, FittingItem

CORP, STRUCTURE = 98000001, 1049588174021
HULL, MODULE, CONTAINER = 587, 2048, 3293
READY_CFG = dict(ready_to_list_owner_type="corporation", ready_to_list_owner_id=CORP,
                 ready_to_list_location_id=STRUCTURE, ready_to_list_hangar_flag="CorpSAG5")
HANGAR = storage.ExcludedHangar("corporation", CORP, STRUCTURE, "CorpSAG5")


@pytest.fixture(autouse=True)
def _reset_cache():
    ready_to_list.invalidate_cache()
    yield
    ready_to_list.invalidate_cache()


def _candidate(fitting_id="f1", name="Fit", items=None, hull=HULL, doctrine_id="d1"):
    items = items if items is not None else [FittingItem(fitting_id, 1, "low", MODULE, 1)]
    fitting = Fitting(fitting_id=fitting_id, doctrine_id=doctrine_id, name=name,
                      hull_type_id=hull, raw_eft="")
    exact, consume = v.build_contract_soll(items)
    return _Candidate(fitting=fitting, exact_soll=exact, consume_soll=consume, items=items)


def _asset(item_id, type_id, *, location=STRUCTURE, flag="CorpSAG5", qty=1,
           assembled=False, fitted_flag=None, owner=CORP, **extra):
    loc_flag = fitted_flag or flag
    return {"item_id": item_id, "type_id": type_id, "quantity": qty,
            "owner_type": "corporation", "owner_id": owner, "owner_name": "Corp",
            "location_id": location, "location_flag": loc_flag,
            "resolved_location_id": STRUCTURE if fitted_flag is None else STRUCTURE,
            "resolved_hangar_flag": flag if fitted_flag is None else flag,
            "assembled_or_fitted": assembled or bool(fitted_flag),
            "is_singleton": assembled, **extra}


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("overrides", [
    {"ready_to_list_owner_type": "corporation"},
    {**READY_CFG, "ready_to_list_location_id": None},
    {**READY_CFG, "ready_to_list_owner_type": "alliance"},
    {**READY_CFG, "ready_to_list_hangar_flag": "Deliveries"},
])
def test_invalid_hangar_settings_are_rejected(overrides):
    with pytest.raises(ConfigError):
        validate_doctrine_overrides(overrides)


def test_a_complete_hangar_or_none_at_all_is_accepted():
    validate_doctrine_overrides(READY_CFG)
    validate_doctrine_overrides({key: None for key in READY_CFG})


def test_a_container_without_its_hangar_is_rejected():
    with pytest.raises(ConfigError):
        validate_doctrine_overrides({"ready_to_list_owner_type": None, "ready_to_list_owner_id": None,
                                     "ready_to_list_location_id": None, "ready_to_list_hangar_flag": None,
                                     "ready_to_list_container_id": CONTAINER})
    validate_doctrine_overrides({**READY_CFG, "ready_to_list_container_id": CONTAINER})


def test_configured_hangar_all_or_none():
    assert ready_to_list.configured_hangar(DoctrineConfig()) is None
    assert ready_to_list.configured_hangar(DoctrineConfig(**READY_CFG)) == HANGAR


# -------------------------------------------------------------------- counting
def _setup_counts(monkeypatch, assets, *, shared=True, candidates=None, cfg=None):
    from eve_trader.esi_data import access

    cfg = cfg or DoctrineConfig(**READY_CFG)
    candidates = candidates or [_candidate()]
    monkeypatch.setattr(access, "is_shared",
                        lambda kind, tool, ot, oid: shared and kind == "assets" and tool == "doctrine")
    monkeypatch.setattr(access, "read_esi", lambda *a, **k: assets)
    monkeypatch.setattr(engine, "equivalents_of", lambda _cfg=None: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda t: (t, 1, f"T{t}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_slot", lambda t: "low")
    return ready_to_list.counts(cfg, candidates), candidates


def test_no_hangar_returns_none(monkeypatch):
    from eve_trader.esi_data import access
    monkeypatch.setattr(access, "is_shared", lambda *a, **k: True)
    assert ready_to_list.counts(DoctrineConfig(), [_candidate()]) is None


def test_no_asset_share_returns_none(monkeypatch):
    counts, _ = _setup_counts(monkeypatch, [], shared=False)
    assert counts is None


def test_empty_hangar_returns_zeros(monkeypatch):
    counts, cands = _setup_counts(monkeypatch, [])
    assert counts == {cands[0].fitting.fitting_id: 0}


def test_valid_and_tolerable_ships_count(monkeypatch):
    # Ship 1: exact match -> valid. Ship 2: cargo short within tolerance -> tolerable.
    # Ship 3: missing module -> invalid, ignored.
    cargo = 300
    cand = _candidate(items=[
        FittingItem("f1", 1, "low", MODULE, 1),
        FittingItem("f1", 2, "cargo", cargo, 10),
    ])
    assets = [
        _asset(10, HULL, assembled=True),
        _asset(11, MODULE, location=10, fitted_flag="LoSlot0", assembled=True),
        _asset(12, cargo, location=10, fitted_flag=None, flag="CorpSAG5", qty=10),
        # second ship: only 9 cargo (tolerable at 90%)
        _asset(20, HULL, assembled=True),
        _asset(21, MODULE, location=20, fitted_flag="LoSlot0", assembled=True),
        _asset(22, cargo, location=20, qty=9),
        # third ship: no module
        _asset(30, HULL, assembled=True),
        _asset(32, cargo, location=30, qty=10),
    ]
    # Fix resolved flags for hangar membership of ships (assembled hulls sit in hangar)
    for ship_id in (10, 20, 30):
        for a in assets:
            if a["item_id"] == ship_id:
                a["resolved_location_id"] = STRUCTURE
                a["resolved_hangar_flag"] = "CorpSAG5"
                a["location_flag"] = "CorpSAG5"
    # Fitted/cargo children: hangar membership uses parent ship; their own
    # resolved_* still name the structure for is_in_hangar when no container.
    for a in assets:
        if a["item_id"] not in (10, 20, 30):
            a["resolved_location_id"] = STRUCTURE
            a["resolved_hangar_flag"] = "CorpSAG5"

    counts, _ = _setup_counts(monkeypatch, assets, candidates=[cand],
                              cfg=DoctrineConfig(**READY_CFG, cargo_tolerance_pct=0.9))
    assert counts == {"f1": 2}


def test_identical_fits_pool_the_same_ship(monkeypatch):
    items = [FittingItem("f1", 1, "low", MODULE, 1)]
    c1 = _candidate("f1", "Fit A", items=items, doctrine_id="d1")
    c2 = _candidate("f2", "Fit B", items=[FittingItem("f2", 1, "low", MODULE, 1)], doctrine_id="d2")
    assets = [
        _asset(10, HULL, assembled=True, flag="CorpSAG5"),
        _asset(11, MODULE, location=10, fitted_flag="LoSlot0", assembled=True),
    ]
    for a in assets:
        a["resolved_location_id"] = STRUCTURE
        a["resolved_hangar_flag"] = "CorpSAG5"
        if a["item_id"] == 10:
            a["location_flag"] = "CorpSAG5"

    counts, _ = _setup_counts(monkeypatch, assets, candidates=[c1, c2])
    assert counts == {"f1": 1, "f2": 1}


def test_container_narrows_which_ships_count(monkeypatch):
    from eve_trader.esi_data import access

    cfg = DoctrineConfig(**READY_CFG, ready_to_list_container_id=CONTAINER)
    cand = _candidate()
    # Ship inside the container vs ship loose in the hangar.
    assets = [
        _asset(CONTAINER, 3293, flag="CorpSAG5"),
        _asset(10, HULL, location=CONTAINER, assembled=True, flag="CorpSAG5"),
        _asset(11, MODULE, location=10, fitted_flag="LoSlot0", assembled=True),
        _asset(20, HULL, assembled=True, flag="CorpSAG5"),
        _asset(21, MODULE, location=20, fitted_flag="LoSlot0", assembled=True),
    ]
    for a in assets:
        a["resolved_location_id"] = STRUCTURE
        a["resolved_hangar_flag"] = "CorpSAG5"
        if a["item_id"] in (10, 20, CONTAINER):
            a["location_flag"] = "CorpSAG5" if a["item_id"] != 10 else "Unlocked"
            if a["item_id"] == 10:
                a["location_flag"] = "Unlocked"
            if a["item_id"] == 20:
                a["location_flag"] = "CorpSAG5"

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "t1")
    monkeypatch.setattr(storage, "asset_subtree_item_ids",
                        lambda *a: frozenset({CONTAINER, 10, 11}))
    monkeypatch.setattr(access, "is_shared", lambda *a, **k: True)
    monkeypatch.setattr(access, "read_esi", lambda *a, **k: assets)
    monkeypatch.setattr(engine, "equivalents_of", lambda _cfg=None: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda t: (t, 1, f"T{t}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_type_slot", lambda t: "low")

    counts = ready_to_list.counts(cfg, [cand])
    assert counts == {"f1": 1}


def test_missing_container_returns_zeros(monkeypatch):
    from eve_trader.esi_data import access

    cfg = DoctrineConfig(**READY_CFG, ready_to_list_container_id=999)
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "t1")
    monkeypatch.setattr(storage, "asset_subtree_item_ids", lambda *a: frozenset())
    monkeypatch.setattr(access, "is_shared", lambda *a, **k: True)
    monkeypatch.setattr(access, "read_esi", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no read")))
    assert ready_to_list.counts(cfg, [_candidate()]) == {"f1": 0}


def test_fitting_status_carries_ready_to_list(monkeypatch):
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [])
    monkeypatch.setattr(storage, "newest_esi_freshness_success_for_tool", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda t: (t, 1, "Hull", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [_candidate()])
    monkeypatch.setattr(ready_to_list, "counts", lambda *a, **k: {"f1": 3})

    status = engine.fitting_status(
        Fitting(fitting_id="f1", doctrine_id="d1", name="Fit", hull_type_id=HULL, raw_eft=""),
        stockpile_rows=[], assets_available=True)
    assert status.ready_to_list == 3


def test_fitting_status_null_when_counts_unavailable(monkeypatch):
    monkeypatch.setattr(storage, "list_doctrine_contracts", lambda **k: [])
    monkeypatch.setattr(storage, "newest_esi_freshness_success_for_tool", lambda *a, **k: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda t: (t, 1, "Hull", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(engine, "load_match_candidates", lambda: [_candidate()])
    monkeypatch.setattr(ready_to_list, "counts", lambda *a, **k: None)

    status = engine.fitting_status(
        Fitting(fitting_id="f1", doctrine_id="d1", name="Fit", hull_type_id=HULL, raw_eft=""),
        stockpile_rows=[], assets_available=True)
    assert status.ready_to_list is None


def test_settings_expose_ready_to_list_fields():
    cfg = DoctrineConfig(**READY_CFG, ready_to_list_container_id=CONTAINER)
    out = actions.do_get_settings(cfg)
    assert out["ready_to_list_owner_type"] == "corporation"
    assert out["ready_to_list_owner_id"] == CORP
    assert out["ready_to_list_location_id"] == STRUCTURE
    assert out["ready_to_list_hangar_flag"] == "CorpSAG5"
    assert out["ready_to_list_container_id"] == CONTAINER


def test_ready_hangar_change_invalidates_cache(monkeypatch):
    called = []
    monkeypatch.setattr(ready_to_list, "invalidate_cache", lambda: called.append(True))
    monkeypatch.setattr(actions, "_reject_intake_hangar", lambda u: None)
    monkeypatch.setattr(actions, "_reject_foreign_container", lambda *a, **k: None)
    monkeypatch.setattr(actions, "save_tenant_config_overrides",
                        lambda *a, **k: None)
    # Patch the import site used inside do_update_settings
    import eve_trader.config as config_mod
    monkeypatch.setattr(config_mod, "save_tenant_config_overrides",
                        lambda *a, **k: None)
    monkeypatch.setattr("eve_trader.doctrine.actions.save_tenant_config_overrides",
                        lambda *a, **k: None)

    actions.do_update_settings(READY_CFG, DoctrineConfig())
    assert called == [True]

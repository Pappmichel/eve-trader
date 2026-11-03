"""production/max_buildable.py - "how many can I build right now".

Synthetic BOM, every storage/engine read monkeypatched (no Postgres):
    Ship (1)  <- bp 101: Comp x10, Mineral x100
    Comp (2)  <- bp 102: Mineral x5, Moon x2
    Mineral (3), Moon (4): no recipe
"""
import pytest

from eve_trader import storage
from eve_trader.production import engine, max_buildable
from eve_trader.production.config import ProductionConfig

SHIP, COMP, MINERAL, MOON = 1, 2, 3, 4
NAMES = {SHIP: "Ship", COMP: "Comp", MINERAL: "Mineral", MOON: "Moon", 101: "Ship Blueprint", 102: "Comp Blueprint"}
RECIPES = {SHIP: (101, 1, 1.0), COMP: (102, 1, 1.0)}
MATERIALS = {101: [(COMP, 10.0), (MINERAL, 100.0)], 102: [(MINERAL, 5.0), (MOON, 2.0)]}


@pytest.fixture
def bom(monkeypatch):
    state = {
        "stock": {},
        "originals": {101: (10, 20), 102: (10, 20)},
        "copies": {},
        "materials": dict(MATERIALS),
    }
    monkeypatch.setattr(engine, "classify_activity",
                        lambda type_id: ("Tech I", RECIPES[type_id]) if type_id in RECIPES else ("Input", None))
    monkeypatch.setattr(storage, "get_blueprint_materials",
                        lambda blueprint_id, activity_id: state["materials"].get(blueprint_id, []))
    monkeypatch.setattr(engine, "_stock_on_hand", lambda type_id, manual_stock, cfg: state["stock"].get(type_id, 0.0))
    monkeypatch.setattr(engine, "_owned_bpo_best_me_te", lambda bp_id: state["originals"].get(bp_id))
    monkeypatch.setattr(engine, "_activity_mods",
                        lambda activity, type_id, cfg, cost_indices, blueprint_id=None: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "shared_production_owner_ids", lambda kind: ([], []))
    monkeypatch.setattr(storage, "list_blueprint_copies",
                        lambda bp_id, owner_character_ids=None, owner_corporation_ids=None: state["copies"].get(bp_id, []))
    monkeypatch.setattr(storage, "load_manual_stock", lambda: {})
    monkeypatch.setattr(storage, "load_manual_blueprint_me_te_overrides", lambda: [])
    monkeypatch.setattr(engine, "_sde_type_name", lambda type_id: NAMES.get(type_id, str(type_id)))
    monkeypatch.setattr(max_buildable._Context, "_structure_me_mult", lambda self, activity, type_id: 1.0)
    return state


def _run(mode="full", target=None):
    return max_buildable.max_buildable(SHIP, ProductionConfig(), mode=mode, target_quantity=target)


def _row(rows, type_id):
    return next(r for r in rows if r["type_id"] == type_id)


def test_direct_mode_reports_missing_component_even_when_its_inputs_exist(bom):
    bom["stock"] = {MINERAL: 10_000, MOON: 1_000_000}
    result = _run("direct")
    assert result["max_quantity"] == 0
    assert [r["type_id"] for r in result["bottlenecks"]] == [COMP]
    assert result["bottlenecks"][0]["reason"] == "not_built_direct_mode"


def test_full_mode_builds_the_component_and_the_raw_material_becomes_the_bottleneck(bom):
    # Per ship: 100 Mineral direct + 10 Comp x 5 Mineral = 150 Mineral.
    bom["stock"] = {MINERAL: 10_000, MOON: 1_000_000}
    result = _run("full")
    assert result["max_quantity"] == 66
    assert result["bottleneck_quantity"] == 67
    mineral = _row(result["bottlenecks"], MINERAL)
    assert mineral["needed"] == 67 * 150
    assert mineral["short"] == 67 * 150 - 10_000
    assert mineral["reason"] == "material"
    comp = _row(result["requirements"], COMP)
    assert comp["built"] == 660 and comp["runs"] == 660
    assert _row(result["requirements"], MOON)["needed"] == 1320


def test_component_stock_is_used_before_building_more(bom):
    bom["stock"] = {COMP: 100, MINERAL: 1_000, MOON: 0}
    # 10 ships use the 100 Comp on hand (no Moon needed) and 1,000 Mineral.
    result = _run("full")
    assert result["max_quantity"] == 10
    comp = _row(result["requirements"], COMP)
    assert comp["from_stock"] == 100 and comp["built"] == 0


def test_blueprint_copy_runs_cap_the_result(bom):
    bom["originals"] = {102: (10, 20)}
    bom["copies"] = {101: [(0, 1, 5)]}
    bom["stock"] = {COMP: 10_000, MINERAL: 10_000_000}
    result = _run("full")
    assert result["max_quantity"] == 5
    assert result["blueprint"]["source"] == "copies"
    assert result["blueprint"]["copy_runs"] == 5
    top = _row(result["bottlenecks"], SHIP)
    assert top["reason"] == "blueprint_runs"
    # Materials alone would allow 1,000 (10,000 Comp / 10 per ship).
    assert result["material_max_quantity"] == 1000


def test_each_copy_is_its_own_job_with_its_own_rounding(bom):
    # ME 10 -> 15 * 0.9 = 13.5 per one-run job, rounded up to 14 per copy.
    # Two copies need 28, not the pooled ceil(27.0) = 27.
    bom["originals"] = {}
    bom["copies"] = {101: [(10, 1, 2)]}
    bom["materials"] = {101: [(MINERAL, 15.0)]}
    bom["stock"] = {MINERAL: 27}
    result = _run("full")
    assert result["max_quantity"] == 1
    assert _row(result["bottlenecks"], MINERAL)["needed"] == 28


def test_component_without_owned_blueprint_is_a_bottleneck_in_full_mode(bom):
    bom["originals"] = {101: (10, 20)}
    bom["stock"] = {MINERAL: 10_000, MOON: 1_000_000}
    result = _run("full")
    assert result["max_quantity"] == 0
    assert _row(result["bottlenecks"], COMP)["reason"] == "no_blueprint"


def test_item_without_blueprint_returns_zero_but_still_lists_its_materials(bom):
    bom["originals"] = {102: (10, 20)}
    bom["stock"] = {MINERAL: 10_000, MOON: 1_000_000}
    result = _run("full")
    assert result["max_quantity"] == 0
    assert result["blueprint"]["source"] == "none"
    assert [r["type_id"] for r in result["bottlenecks"]] == [SHIP]
    assert _row(result["bottlenecks"], SHIP)["reason"] == "no_blueprint"
    assert _row(result["requirements"], MINERAL)["needed"] == 150
    assert result["material_max_quantity"] == 66


def test_material_max_is_not_reported_when_an_original_is_owned(bom):
    bom["stock"] = {MINERAL: 10_000, MOON: 1_000_000}
    assert _run("full")["material_max_quantity"] is None


def test_target_quantity_shows_what_is_missing_for_that_amount(bom):
    bom["stock"] = {MINERAL: 10_000, MOON: 1_000_000}
    result = _run("full", target=100)
    assert result["max_quantity"] == 66
    assert result["requirements_quantity"] == 100
    assert result["requirements_feasible"] is False
    assert _row(result["requirements"], MINERAL)["short"] == 15_000 - 10_000


def test_item_that_cannot_be_built_at_all_raises(bom):
    with pytest.raises(ValueError):
        max_buildable.max_buildable(MINERAL, ProductionConfig())


def test_requirements_list_each_material_under_the_item_that_needs_it(bom):
    bom["stock"] = {MINERAL: 10_000, MOON: 1_000_000}
    rows = _run("full")["requirements"]
    assert [(r["type_id"], r["depth"]) for r in rows] == [(SHIP, 0), (COMP, 1), (MOON, 2), (MINERAL, 1)]

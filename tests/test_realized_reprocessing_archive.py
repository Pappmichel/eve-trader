"""Reprocessing detection, I/O side: stock observations written after an
asset sync (realized/ingest.py), read back by the loader, and a full
rebuild that books a detected reprocessing. The pure detection rules are in
test_realized_reprocessing.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.realized import db as ledger_db
from eve_trader.realized import ingest, loader
from eve_trader.realized.costing import ReprocessRecipe

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_DOCS = Path(__file__).resolve().parent.parent / "docs"

ALICE = 920001
BOB = 920002
ORE = 990901
COMP = 990902
MINERAL = 990903
ORE_GROUP = 990950
MINERAL_GROUP = 990951
GAS = 990904
COMP_GAS = 990905
GAS_GROUP = 990952
LOC = 60003760
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

_TABLES = (
    "ledger_wallet_transactions", "ledger_wallet_journal", "ledger_industry_jobs", "ledger_job_inputs",
    "ledger_market_orders", "ledger_contracts", "ledger_contract_items", "ledger_reference_prices",
    "ledger_valuation_prices", "ledger_sync_state", "ledger_archive_gaps",
    "ledger_stock_observation_runs", "ledger_stock_observations", "prod_reprocess_events",
    "prod_cost_layers", "prod_realized_results", "prod_cost_draws", "prod_job_records",
    "prod_fee_lines", "prod_job_losses", "prod_capital_bpos", "prod_inventory_drift",
    "prod_opening_frozen", "prod_opening_lines", "prod_ledger_runs",
    "esi_sharing", "character_assets", "corp_assets", "module_reprocessing_shortlist",
)


@pytest.fixture(scope="session")
def _apply_ledger_schema(_apply_esi_access_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        for name in ("production_ledger_schema.sql", "special_orders_schema.sql",
                     "module_reprocessing_schema.sql"):
            conn.execute((_DOCS / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _seed(_apply_ledger_schema):
    pg_helpers.wipe_tables(*_TABLES)
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        cur = conn.cursor()
        cur.executemany("INSERT INTO sde_groups VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        [(ORE_GROUP, 25, "Test Ore"), (MINERAL_GROUP, 4, "Test Mineral"),
                         (GAS_GROUP, 2, "Test Cloud"), (4168, 2, "Compressed Gas")])
        cur.executemany(
            "INSERT INTO sde_types (type_id, group_id, type_name, volume, published) "
            "VALUES (%s,%s,%s,%s,1) ON CONFLICT (type_id) DO NOTHING",
            [(ORE, ORE_GROUP, "Testite", 0.1), (COMP, ORE_GROUP, "Compressed Testite", 0.001),
             (MINERAL, MINERAL_GROUP, "Testium", 0.01), (GAS, GAS_GROUP, "Testerene", 5.0),
             (COMP_GAS, 4168, "Compressed Testerene", 0.5)],
        )
        cur.execute("UPDATE sde_types SET portion_size = 1 WHERE type_id = %s", (COMP_GAS,))
        cur.execute("INSERT INTO sde_type_materials (type_id, material_type_id, quantity) "
                    "VALUES (%s, %s, 1) ON CONFLICT DO NOTHING", (COMP_GAS, GAS))
    yield
    pg_helpers.wipe_tables(*_TABLES)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(loader, "_planner_values", lambda type_ids: {})
    monkeypatch.setattr(loader, "_fetch_reference_prices", lambda requests, today: {})
    from eve_trader.realized import names
    monkeypatch.setattr(names, "_esi_names", lambda ids: {})


def _share(owner_id: int, *kinds: str, tool: str = "production"):
    with storage.connect() as conn:
        for kind in kinds:
            conn.execute(
                "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
                (owner_id, kind, tool),
            )


def _assets(owner_id: int, rows: list[tuple]):
    """rows: (item_id, type_id, quantity, assembled_or_fitted)."""
    with storage.connect() as conn:
        conn.execute("DELETE FROM character_assets WHERE owner_character_id = ?", (owner_id,))
        for item_id, type_id, qty, fitted in rows:
            conn.execute(
                "INSERT INTO character_assets (item_id, type_id, location_id, location_flag, quantity, "
                "owner_name, owner_character_id, assembled_or_fitted) VALUES (?,?,?,?,?,?,?,?)",
                (item_id, type_id, LOC, "Hangar", qty, f"c{owner_id}", owner_id, fitted),
            )


def _observation_rows():
    with storage.connect() as conn:
        runs = conn.execute("SELECT count(*) FROM ledger_stock_observation_runs").fetchone()[0]
        rows = conn.execute(
            "SELECT type_id, observed_at, quantity FROM ledger_stock_observations ORDER BY observed_at, type_id"
        ).fetchall()
    return runs, [(int(t), q) for t, _at, q in rows]


# ---------------------------------------------------------------- archive
def test_observation_counts_every_owner_and_writes_only_changes(tenant):
    _share(ALICE, "assets", "market_orders")
    _share(BOB, "assets")
    _assets(ALICE, [(1, ORE, 1000, False), (2, MINERAL, 50, False)])
    _assets(BOB, [(3, ORE, 200, True)])  # fitted/assembled still exists

    assert ingest.archive_stock_observation(now=T0) == 1
    assert ingest.archive_stock_observation(now=T0 + timedelta(hours=1)) == 0
    _assets(ALICE, [(2, MINERAL, 50, False)])
    _assets(BOB, [])
    assert ingest.archive_stock_observation(now=T0 + timedelta(hours=2)) == 1

    runs, rows = _observation_rows()
    assert runs == 3
    # Minerals are not observed; ore 1200, then 0.
    assert rows == [(ORE, 1200.0), (ORE, 0.0)]


def test_compressed_gas_is_observed_and_decompresses_at_the_configured_yield(tenant, monkeypatch):
    from eve_trader.refining.config import REFINING_CONFIG
    _share(ALICE, "assets")
    _assets(ALICE, [(1, COMP_GAS, 400, False), (2, GAS, 90, False)])
    ingest.archive_stock_observation(now=T0)
    assert _observation_rows()[1] == [(COMP_GAS, 400.0)]   # raw gas is not observed

    monkeypatch.setattr(REFINING_CONFIG, "gas_decompression_yield", 0.95)
    recipes = loader._reprocess_recipes({COMP_GAS, GAS})
    assert set(recipes) == {COMP_GAS}
    assert recipes[COMP_GAS].outputs(400) == {GAS: 380.0}


def test_gas_decompressed_before_the_series_began_is_still_detected(tenant, monkeypatch):
    """The series never held the compressed gas (it was gone before the
    first observation), so only the ledger knows the type."""
    from eve_trader.refining.config import REFINING_CONFIG
    monkeypatch.setattr(REFINING_CONFIG, "gas_decompression_yield", 0.95)
    _share(ALICE, "wallet", "market_orders", "assets")
    monkeypatch.setattr(loader, "_tenant_character_ids", lambda: frozenset({ALICE}))
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, COMP_GAS, LOC, 10.0, 400, True, 101, None, None, None),
    ])
    _assets(ALICE, [(6, GAS, 380, False)])
    ingest.archive_stock_observation(now=T0 + timedelta(days=1))
    assert _observation_rows()[1] == []

    result = loader.rebuild_realized()
    assert result["reprocess_events"] == 1
    [event] = ledger_db.list_reprocess_events()
    assert int(event[2]) == COMP_GAS and float(event[3]) == 400
    [output] = event[14]
    assert (output["type_id"], output["quantity"]) == (GAS, 380.0)
    assert output["unit_cost"] == pytest.approx(4000.0 / 380.0)


def test_new_observed_types_start_a_new_series(tenant, monkeypatch):
    from eve_trader.realized import access
    _share(ALICE, "assets")
    current = access.assets_scope_hash()
    monkeypatch.setattr(access, "OBSERVED_TYPES_VERSION", access.OBSERVED_TYPES_VERSION + 1)
    assert access.assets_scope_hash() != current


def test_observation_skips_owners_not_shared_with_a_realized_tool(tenant):
    _share(ALICE, "assets", tool="char_info")
    _assets(ALICE, [(1, ORE, 1000, False)])
    assert ingest.archive_stock_observation(now=T0) == 0
    assert _observation_rows() == (0, [])


def test_shortlisted_module_is_observed(tenant):
    _share(ALICE, "assets")
    with storage.connect() as conn:
        conn.execute("INSERT INTO module_reprocessing_shortlist (item_id, item, active) VALUES (?, 'm', true)",
                     (MINERAL,))
    _assets(ALICE, [(2, MINERAL, 7, False)])
    ingest.archive_stock_observation(now=T0)
    assert _observation_rows()[1] == [(MINERAL, 7.0)]


# ---------------------------------------------------------------- loader
def test_loader_carries_quantities_forward_and_starts_at_t0(tenant):
    _share(ALICE, "assets")
    _assets(ALICE, [(1, ORE, 500, False), (4, COMP, 3, False)])
    ingest.archive_stock_observation(now=T0 - timedelta(days=1))
    _assets(ALICE, [(1, ORE, 400, False), (4, COMP, 3, False)])
    ingest.archive_stock_observation(now=T0 + timedelta(hours=1))
    ingest.archive_stock_observation(now=T0 + timedelta(hours=2))

    observations, held = loader._stock_observations(T0)
    assert held == {ORE, COMP}
    assert [o.observed_at for o in observations] == [T0 + timedelta(hours=1), T0 + timedelta(hours=2)]
    assert [dict(o.quantities) for o in observations] == [{ORE: 400.0, COMP: 3.0}] * 2


def test_detection_is_blocked_when_a_wallet_owner_hides_its_assets(tenant):
    _share(ALICE, "wallet", "market_orders", "assets")
    _share(BOB, "wallet")
    reason = loader._reprocess_blocked()
    assert reason is not None and str(BOB) in reason and "assets" in reason
    _share(BOB, "assets", "market_orders")
    assert loader._reprocess_blocked() is None


def test_compression_link_ratio_comes_from_the_sde_recipes(tenant):
    recipes = {
        ORE: ReprocessRecipe(ORE, 100, ((MINERAL, 400),), 0.5),
        COMP: ReprocessRecipe(COMP, 1, ((MINERAL, 400),), 0.5),
    }
    [link] = loader._compression_links(recipes)
    assert (link.raw_type_id, link.compressed_type_id) == (ORE, COMP)
    assert link.ratio == pytest.approx(0.01)
    # Materials in a different proportion: no link.
    recipes[ORE] = ReprocessRecipe(ORE, 100, ((MINERAL, 400), (ORE_GROUP, 100)), 0.5)
    recipes[COMP] = ReprocessRecipe(COMP, 1, ((MINERAL, 400), (ORE_GROUP, 300)), 0.5)
    assert loader._compression_links(recipes) == []


def test_rebuild_books_detected_reprocessing_and_resets_opening_once(tenant, monkeypatch):
    _share(ALICE, "wallet", "market_orders", "assets")
    monkeypatch.setattr(loader, "_reprocess_recipes", lambda types: {
        ORE: ReprocessRecipe(ORE, 100, ((MINERAL, 400),), 0.5),
    })
    monkeypatch.setattr(loader, "_tenant_character_ids", lambda: frozenset({ALICE}))
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, ORE, LOC, 10.0, 1000, True, 101, None, None, None),
    ])
    ledger_db.upsert_reference_prices([(MINERAL, (T0 + timedelta(days=1)).date(), 5.0, "test")])

    first = loader.rebuild_realized()  # no observation yet
    assert first["reprocess_events"] == 0
    assert ledger_db.latest_costing_version()[2] is None

    _assets(ALICE, [(1, ORE, 1000, False)])
    ingest.archive_stock_observation(now=T0 + timedelta(days=1))
    _assets(ALICE, [(5, MINERAL, 2000, False)])
    ingest.archive_stock_observation(now=T0 + timedelta(days=2))

    second = loader.rebuild_realized()
    assert second["reprocess_events"] == 1
    baseline = ledger_db.latest_costing_version()[2]
    assert baseline == T0 + timedelta(days=1)
    [event] = ledger_db.list_reprocess_events()
    assert event[1] == "reprocess" and int(event[2]) == ORE and float(event[3]) == 1000
    [output] = event[14]
    assert (output["type_id"], output["quantity"], output["unit_cost"]) == (MINERAL, 2000.0, 5.0)
    with storage.connect() as conn:
        layers = conn.execute(
            "SELECT type_id, origin, quantity_left, unit_cost FROM prod_cost_layers ORDER BY type_id"
        ).fetchall()
        draws = conn.execute(
            "SELECT consumer_kind, type_id, quantity FROM prod_cost_draws WHERE consumer_kind = 'reprocess'"
        ).fetchall()
        opening = conn.execute(
            "SELECT raw_quantity FROM prod_opening_frozen WHERE type_id = ?", (MINERAL,)
        ).fetchone()
    # The ore is gone; its 10,000 ISK sit on the minerals.
    assert [(int(t), o, float(q), float(u)) for t, o, q, u in layers] == [(MINERAL, "reprocessed", 2000.0, 5.0)]
    assert [(k, int(t), float(q)) for k, t, q in draws] == [("reprocess", ORE, 1000.0)]
    # Minerals in stock are the reprocessed ones, not opening.
    assert opening is not None and float(opening[0]) == pytest.approx(0.0)
    # Same baseline: the next rebuild does not reset the opening again.
    assert loader._baseline_changed(baseline) is False


# ---------------------------------------------------------------- report
def _rebuild_with_one_reprocessing(monkeypatch):
    _share(ALICE, "wallet", "market_orders", "assets")
    monkeypatch.setattr(loader, "_reprocess_recipes", lambda types: {
        ORE: ReprocessRecipe(ORE, 100, ((MINERAL, 400),), 0.5),
    })
    monkeypatch.setattr(loader, "_tenant_character_ids", lambda: frozenset({ALICE}))
    ledger_db.upsert_ledger_wallet_transactions([
        ("character", ALICE, 0, 1, T0, ORE, LOC, 10.0, 1000, True, 101, None, None, None),
    ])
    ledger_db.upsert_reference_prices([(MINERAL, (T0 + timedelta(days=1)).date(), 5.0, "test")])
    _assets(ALICE, [(1, ORE, 1000, False)])
    ingest.archive_stock_observation(now=T0 + timedelta(days=1))
    _assets(ALICE, [(5, MINERAL, 2000, False)])
    ingest.archive_stock_observation(now=T0 + timedelta(days=2))
    loader.rebuild_realized()


def test_reprocessing_list_shows_cost_and_outputs(tenant, monkeypatch):
    from eve_trader.production.realized import report

    _rebuild_with_one_reprocessing(monkeypatch)
    data = report.reprocessing()
    assert data["needs_rebuild"] is False
    assert data["observed_since"] is not None
    [row] = data["rows"]
    assert (row["kind"], row["type_id"], row["type_name"], row["quantity"]) == ("reprocess", ORE, "Testite", 1000.0)
    assert row["cost"] == pytest.approx(10000.0)
    assert row["cost_per_unit"] == pytest.approx(10.0)
    assert row["outputs"] == [{"type_id": MINERAL, "type_name": "Testium", "quantity": 2000.0, "unit_cost": 5.0}]
    assert row["flags"]["estimated"] is False


def test_cost_chain_walks_from_minerals_back_to_the_ore_purchase(tenant, monkeypatch):
    from eve_trader.production.realized import report

    _rebuild_with_one_reprocessing(monkeypatch)
    [event] = ledger_db.list_reprocess_events()
    layer_id = event[14][0]["layer_id"]
    # A draw of 1000 minerals (half of the output) as a sale or job would store it.
    draw = ("job:x", 0, layer_id, MINERAL, "item", 1000.0, "reprocessed", event[0],
            5000.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0, False, False, False, False,
            T0 + timedelta(days=1), None, None, None, None, None, None, None)
    [node] = report._children([draw], 0, set())
    assert node["kind"] == "reprocess"
    assert node["input_type_id"] == ORE
    assert node["share"] == pytest.approx(0.5)
    assert node["cost_share"] == pytest.approx(5000.0)
    [ore] = node["children"]
    assert (ore["origin"], ore["type_id"], ore["quantity"]) == ("bought", ORE, pytest.approx(500.0))

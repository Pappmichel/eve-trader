"""Asset Manager (docs/ASSET_MANAGER_PLAN.md): owner union, row sources,
paths, badges, valuation and the query layer against a real database."""
from __future__ import annotations

import pytest

from eve_trader import portfolio, storage
from eve_trader.actions import ActionError
from eve_trader.asset_manager import actions as am_actions
from eve_trader.asset_manager import query, scope

from . import pg_helpers
from .pg_helpers import _apply_esi_access_schema, _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE, BOB, CAROL, CORP = 91001, 91002, 91003, 94001
STATION = 60003760
SYSTEM, REGION = 30000142, 10000002
STRUCTURE = 1035466617946
TRIT, SHIP, CONTAINER, WRAP, BPC, PRODUCT, BP = 34, 990601, 990602, 60, 990603, 990604, 990605
OFFICE = storage.OFFICE_TYPE_ID

_TABLES = (
    "character_assets", "corp_assets", "character_blueprints", "corp_blueprints",
    "character_industry_jobs", "corp_industry_jobs", "character_sell_orders", "esi_sharing",
    "manual_stock", "structure_names", "global_structure_names", "manual_location_names",
)


@pytest.fixture(autouse=True)
def _seed_sde(_apply_esi_access_schema):
    pg_helpers.wipe_tables(*_TABLES)
    am_actions.invalidate_cache()
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        for sql, rows in (
            ("INSERT INTO sde_categories VALUES (%s,%s) ON CONFLICT DO NOTHING",
             [(4, "Material"), (6, "Ship"), (9, "Blueprint"), (2, "Celestial")]),
            ("INSERT INTO sde_groups VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
             [(18, 4, "Mineral"), (990025, 6, "Frigate"), (990448, 2, "Cargo Container"),
              (990105, 9, "Frigate Blueprint")]),
            ("INSERT INTO sde_types (type_id, group_id, type_name, volume, published) VALUES (%s,%s,%s,%s,1) "
             "ON CONFLICT (type_id) DO NOTHING",
             [(TRIT, 18, "Tritanium", 0.01), (SHIP, 990025, "Test Rifter", 27289.0),
              (CONTAINER, 990448, "Test Container", 65.0), (BPC, 990105, "Test Rifter Blueprint", 0.01),
              (PRODUCT, 18, "Test Product", 1.0), (BP, 990105, "Test Product Blueprint", 0.01)]),
            ("INSERT INTO type_packaged_volume VALUES (%s,%s) ON CONFLICT DO NOTHING", [(SHIP, 2500.0)]),
            ("INSERT INTO sde_regions VALUES (%s,%s) ON CONFLICT DO NOTHING", [(REGION, "The Forge")]),
            ("INSERT INTO sde_solar_systems VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
             [(SYSTEM, "Jita", 0.9459, REGION)]),
            ("INSERT INTO sde_stations VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
             [(STATION, SYSTEM, "Jita IV - Moon 4")]),
            ("INSERT INTO sde_blueprint_products VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
             [(BP, 1, PRODUCT, 10)]),
        ):
            conn.cursor().executemany(sql, rows)
    yield
    pg_helpers.wipe_tables(*_TABLES)
    am_actions.invalidate_cache()


@pytest.fixture(autouse=True)
def _prices(monkeypatch):
    monkeypatch.setattr(portfolio, "_priced", lambda ids: {t: p for t, p in {TRIT: 5.0, SHIP: 1_000_000.0}.items() if t in ids})
    monkeypatch.setattr(portfolio, "_manual_only_priced", lambda ids: {})
    monkeypatch.setattr(portfolio, "_bpc_run_values", lambda ids: {BPC: 100.0} if BPC in ids else {})


@pytest.fixture
def gate_on(monkeypatch):
    grants = {"asset_manager", "doctrine", "characters"}
    monkeypatch.setattr(scope.tenant_eligibility, "granted_tools", lambda tid: set(grants))
    return grants


def _share(owner_type, owner_id, tool_key, *kinds):
    with storage.connect() as conn:
        for kind in kinds or ("assets",):
            conn.execute(
                "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) VALUES (?,?,?,?)",
                (owner_type, owner_id, kind, tool_key),
            )


def _seed_alice():
    storage.replace_assets("character_assets", [
        (1, TRIT, STATION, "Hangar", 1000, 0, "Alice"),
        (2, SHIP, STATION, "Hangar", 1, 0, "Alice", True, "Alice's Rifter"),
        (3, CONTAINER, 2, "Cargo", 1, 0, "Alice", True, "Spare parts"),
        (4, TRIT, 3, "Unlocked", 50, 0, "Alice"),
        (5, WRAP, STATION, "AssetSafety", 1, 0, "Alice", True),
        (6, TRIT, 5, "Hangar", 7, 0, "Alice"),
        (7, BPC, STATION, "Hangar", 1, 1, "Alice", True),
    ], owner_character_id=ALICE, owner_name="Alice")
    storage.replace_blueprints("character_blueprints", [
        (7, BPC, STATION, "Hangar", -2, 2, 4, 10),
    ], owner_character_id=ALICE)


def _rows(**params):
    return am_actions.do_list_items(group_by="none", page_size=500, **params)


def test_union_counts_granted_tools_and_own_share_only(tenant, gate_on):
    _seed_alice()
    storage.replace_assets("character_assets", [(11, TRIT, STATION, "Hangar", 3, 0, "Bob")],
                           owner_character_id=BOB, owner_name="Bob")
    storage.replace_assets("character_assets", [(21, TRIT, STATION, "Hangar", 9, 0, "Carol")],
                           owner_character_id=CAROL, owner_name="Carol")
    _share("character", ALICE, "asset_manager", "assets", "blueprints")
    _share("character", BOB, "doctrine")          # granted tool: counts (D10)
    _share("character", CAROL, "production")      # tool not granted: frozen data, left out (R-6)

    owners = {r["owner_name"] for r in _rows()["rows"]}

    assert owners == {"Alice", "Bob"}


def test_gate_off_reads_every_share(tenant, monkeypatch):
    monkeypatch.setattr(scope.tenant_eligibility, "granted_tools", lambda tid: None)
    storage.replace_assets("character_assets", [(21, TRIT, STATION, "Hangar", 9, 0, "Carol")],
                           owner_character_id=CAROL, owner_name="Carol")
    _share("character", CAROL, "production")

    result = _rows()

    assert [r["owner_name"] for r in result["rows"]] == ["Carol"]
    assert result["manual_stock_visible"] is True


def test_unshared_owner_is_never_read(tenant, gate_on):
    _seed_alice()
    result = _rows()
    assert result["rows"] == []
    assert result["shared"] is False


def test_paths_hangars_badges_and_values(tenant, gate_on):
    _seed_alice()
    _share("character", ALICE, "asset_manager", "assets", "blueprints")

    rows = {r["item_id"]: r for r in _rows()["rows"]}

    nested = rows[4]
    assert nested["container_path"] == ["Alice's Rifter", "Spare parts"]
    assert nested["hangar"] == "Item hangar"
    assert nested["location_name"] == "Jita IV - Moon 4"
    assert nested["solar_system_name"] == "Jita" and nested["region_name"] == "The Forge"
    assert nested["security"] == 0.9
    assert nested["value"] == 250.0
    assert nested["stock"] is True

    ship = rows[2]
    assert ship["item_name"] == "Alice's Rifter"
    assert "assembled" in ship["badges"] and ship["stock"] is False
    assert ship["unit_volume"] == 27289.0  # assembled: SDE volume, not packaged

    wrapped = rows[6]
    assert wrapped["hangar"] == "Asset safety"
    assert "asset_safety" in wrapped["badges"] and wrapped["stock"] is False

    bpc = rows[7]
    assert "bpc" in bpc["badges"]
    assert (bpc["me"], bpc["te"], bpc["runs"]) == (2, 4, 10)
    assert bpc["value"] == 1000.0  # invention cost per run x runs (D3)


def test_corp_office_division_is_the_hangar(tenant, gate_on):
    storage.replace_assets("corp_assets", [
        (900, OFFICE, STATION, "OfficeFolder", 1, 0, "Corp (corp)", True),
        (901, TRIT, 900, "CorpSAG2", 400, 0, "Corp (corp)"),
    ], owner_corporation_id=CORP, owner_name="Corp (corp)")
    _share("corporation", CORP, "asset_manager")

    rows = {r["item_id"]: r for r in _rows()["rows"]}

    assert rows[901]["hangar"] == "Division 2"
    assert rows[901]["container_path"] == []
    assert rows[901]["owner_name"] == "Corp (corp)"


def test_virtual_sources_listed_jobs_and_manual(tenant, gate_on):
    storage.replace_sell_orders([(5001, TRIT, STATION, REGION, 30, "Alice")], owner_character_id=ALICE)
    storage.replace_industry_jobs("character_industry_jobs", [
        (7001, 1, BP, PRODUCT, 3, STATION, "active", "2026-11-01T00:00:00Z", None, ALICE, "Alice"),
        (7002, 8, BP, PRODUCT, 3, STATION, "active", "2026-11-01T00:00:00Z", None, ALICE, "Alice"),
    ], owner_character_id=ALICE)
    storage.upsert_manual_stock(TRIT, 12, 0)
    _share("character", ALICE, "asset_manager", "market_orders", "industry_jobs")

    by_source = {r["source"]: r for r in _rows()["rows"]}

    assert by_source["listed"]["quantity"] == 30 and by_source["listed"]["value"] == 150.0
    assert by_source["in_production"]["quantity"] == 30  # 3 runs x 10 per run; invention job left out
    assert by_source["in_production"]["job_end"] == "2026-11-01T00:00:00Z"
    assert "manual" not in by_source  # manual stock is Production data (R-8)


def test_manual_stock_needs_the_production_grant(tenant, gate_on):
    gate_on.add("production")
    storage.upsert_manual_stock(TRIT, 12, 0)

    rows = _rows()["rows"]

    assert [(r["source"], r["owner_name"], r["location_kind"]) for r in rows] == [("manual", "Manual stock", "none")]


def test_stock_only_filter_and_totals_cover_the_whole_filter(tenant, gate_on):
    _seed_alice()
    _share("character", ALICE, "asset_manager", "assets", "blueprints")

    stock = am_actions.do_list_items(group_by="none", stock_only=True, page_size=1)
    # Hangar stack, the container in the ship's cargo, its content and the
    # BPC - the assembled ship and the Asset Safety content are not stock.
    assert stock["total_rows"] == 4
    assert len(stock["rows"]) == 1
    assert stock["totals"]["quantity"] == 1052
    assert stock["totals"]["value"] == 6250.0
    assert stock["totals"]["unpriced"] == 1  # the container has no price

    grouped = am_actions.do_list_items(group_by="type", type_ids=[TRIT])
    assert grouped["total_rows"] == 1
    assert grouped["rows"][0]["quantity"] == 1057
    assert grouped["rows"][0]["stacks"] == 3


def test_text_search_matches_container_names(tenant, gate_on):
    _seed_alice()
    _share("character", ALICE, "asset_manager", "assets", "blueprints")

    rows = _rows(q="spare trit")["rows"]

    assert [r["item_id"] for r in rows] == [4]


def test_container_filter_uses_the_ancestor_chain(tenant, gate_on):
    _seed_alice()
    _share("character", ALICE, "asset_manager", "assets", "blueprints")

    rows = _rows(container_item_id=2)["rows"]

    assert sorted(r["item_id"] for r in rows) == [3, 4]


def test_structure_location_uses_global_name_and_system(tenant, gate_on):
    storage.replace_assets("character_assets", [(31, TRIT, STRUCTURE, "Hangar", 5, 0, "Alice")],
                           owner_character_id=ALICE, owner_name="Alice")
    storage.upsert_global_structure_name(STRUCTURE, "Some Keepstar", SYSTEM)
    _share("character", ALICE, "asset_manager")

    row = _rows()["rows"][0]

    assert (row["location_name"], row["location_kind"], row["solar_system_name"]) == ("Some Keepstar", "structure", "Jita")


def test_bad_parameters_are_action_errors(tenant, gate_on):
    with pytest.raises(ActionError, match="group_by"):
        am_actions.do_list_items(group_by="nope")
    with pytest.raises(ActionError, match="owner"):
        am_actions.do_list_items(owners=["alliance:1"])
    with pytest.raises(ActionError, match="page_size"):
        am_actions.do_list_items(page_size=10_000)


def test_csv_export_neutralises_formulas(tenant, gate_on):
    storage.replace_assets("character_assets", [
        (2, SHIP, STATION, "Hangar", 1, 0, "Alice", True, "=HYPERLINK(\"x\")"),
    ], owner_character_id=ALICE, owner_name="Alice")
    _share("character", ALICE, "asset_manager")

    body = am_actions.do_export_csv(group_by="none")

    assert "'=HYPERLINK" in body
    assert ",=HYPERLINK" not in body


def test_eve_list_sums_per_type(tenant, gate_on):
    _seed_alice()
    _share("character", ALICE, "asset_manager", "assets", "blueprints")

    text = am_actions.do_export_eve_list(stock_only=True, type_ids=[TRIT])

    assert text == "Tritanium\t1050"


def test_freshness_lists_owners_and_kinds_in_scope(tenant, gate_on):
    _share("character", ALICE, "asset_manager", "assets", "blueprints")

    result = am_actions.do_freshness()

    assert [o["owner_id"] for o in result["owners"]] == [ALICE]
    assert set(result["owners"][0]["kinds"]) == {"assets", "blueprints"}


# ---------------------------------------------------------------- pure query layer
def _row(**kw):
    base = {
        "source": "esi", "owner_type": "character", "owner_id": 1, "owner_name": "A", "item_id": 1,
        "type_id": 34, "type_name": "Tritanium", "quantity": 1.0, "value": None, "volume": None,
        "location_id": 1, "badges": [], "stock": True,
    }
    base.update(kw)
    return base


def test_sort_puts_missing_values_last_in_both_directions():
    rows = [_row(item_id=1, value=None), _row(item_id=2, value=5.0), _row(item_id=3, value=9.0)]
    assert [r["item_id"] for r in query.sort_rows(rows, "value", True)] == [3, 2, 1]
    assert [r["item_id"] for r in query.sort_rows(rows, "value", False)] == [2, 3, 1]


def test_group_counts_unpriced_rows():
    rows = [_row(value=10.0, quantity=2.0), _row(item_id=2, value=None, quantity=3.0)]
    (g,) = query.group(rows, "type")
    assert (g["quantity"], g["value"], g["unpriced"], g["stacks"]) == (5.0, 10.0, 1, 2)


def test_contract_items_carry_the_start_location(tenant, gate_on):
    # Composite-PK tables: the fresh tenant isolates them.
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO ledger_contracts (owner_type, owner_id, contract_id, issuer_id, start_location_id, "
            "type, status) VALUES ('character', ?, 77, ?, ?, 'item_exchange', 'outstanding')",
            (ALICE, ALICE, STATION),
        )
        conn.execute(
            "INSERT INTO ledger_contract_items (owner_type, owner_id, contract_id, record_id, type_id, quantity) "
            "VALUES ('character', ?, 77, 1, ?, 40)",
            (ALICE, TRIT),
        )
    _share("character", ALICE, "asset_manager", "contracts")

    (row,) = _rows()["rows"]

    assert (row["source"], row["quantity"], row["location_name"], row["value"]) == (
        "contract", 40, "Jita IV - Moon 4", 200.0)


def test_owners_shared_with_any(tenant):
    from eve_trader.esi_data.access import AccessorError, owners_shared_with_any
    _share("character", ALICE, "doctrine")
    _share("corporation", CORP, "asset_manager")
    _share("character", BOB, "production")

    assert owners_shared_with_any("assets") == ([ALICE, BOB], [CORP])
    assert owners_shared_with_any("assets", {"doctrine", "asset_manager"}) == ([ALICE], [CORP])
    assert owners_shared_with_any("assets", set()) == ([], [])
    with pytest.raises(AccessorError):
        owners_shared_with_any("mail")
    with pytest.raises(AccessorError):
        owners_shared_with_any("assets", {"not_a_tool"})


def test_sync_kinds_refreshes_only_the_union(tenant, monkeypatch):
    from eve_trader.esi_data import orchestrator
    _share("character", ALICE, "doctrine", "assets", "wallet")
    _share("character", BOB, "production", "assets")
    captured = {}
    monkeypatch.setattr(orchestrator, "_sync", lambda sharing, **kw: captured.setdefault("sharing", sharing))

    orchestrator.do_sync_kinds(("assets",), tool_keys={"doctrine"})

    assert captured["sharing"] == [("character", ALICE, "assets", "doctrine")]


def test_ledger_archive_runs_for_an_asset_manager_only_share(tenant):
    from eve_trader.esi_data import fetchers
    _share("character", ALICE, "asset_manager", "contracts")

    assert fetchers._shared_with_portfolio("contracts", "character", ALICE) is True
    assert fetchers._shared_with_portfolio("contracts", "character", BOB) is False

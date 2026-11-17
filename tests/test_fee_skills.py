"""Market fees from skills and standings (eve_trader/fee_skills.py) and the
fee hints on the Trading, Station Trading and PI Settings pages.

The hints only show a value; a fee changes only through the tool's normal
settings save, and choosing the fee skills character changes no fee.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import actions as trading_actions
from eve_trader import fee_skill_hints, fee_skills, storage
from eve_trader.auth import TokenRecord
from eve_trader.config import TRADING_CONFIG, apply_config_overrides
from eve_trader.esi_data import read_esi
from eve_trader.pi import actions as pi_actions
from eve_trader.pi.config import PI_CONFIG
from eve_trader.realized import access, buy_fees
from eve_trader.station_trading import actions as station_actions
from eve_trader.station_trading.config import STATION_TRADING_CONFIG
from eve_trader.trade_realized import report as trade_report

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_character_management_schema, _apply_esi_access_schema, _apply_phase1_schema,
    _apply_phase2_schema, _apply_production_ledger_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

ALICE = 930001
BOB = 930002
STATION = 60099901        # a test NPC station
STATION_CORP = 1099901
STATION_FACTION = 599901
STRUCTURE = 1_099_999_000_001
SKILLS_SCOPE = "esi-skills.read_skills.v1"
STANDINGS_SCOPE = "esi-characters.read_standings.v1"
NOW = datetime.now(timezone.utc)


# ------------------------------------------------------------- pure formulas
@pytest.mark.parametrize("level,expected", [
    (0, 0.075), (1, 0.06675), (2, 0.0585), (3, 0.05025), (4, 0.042), (5, 0.03375),
])
def test_sales_tax_per_accounting_level(level, expected):
    assert fee_skills.sales_tax_rate(level) == pytest.approx(expected)


def test_broker_fee_from_broker_relations_without_standings():
    assert fee_skills.npc_broker_fee_rate(0) == pytest.approx(0.03)
    assert fee_skills.npc_broker_fee_rate(5) == pytest.approx(0.015)


def test_standings_lower_the_broker_fee():
    # 3 % - 1.5 % - 0.03 % x 10 - 0.02 % x 10 = 1 % (EVE University example)
    assert fee_skills.npc_broker_fee_rate(5, 10.0, 10.0) == pytest.approx(0.01)
    # faction weighs 0.03 % per point, corporation 0.02 %
    assert fee_skills.npc_broker_fee_rate(5, 1.0, 0.0) == pytest.approx(0.0147)
    assert fee_skills.npc_broker_fee_rate(5, 0.0, 1.6) == pytest.approx(0.01468)
    # a negative raw standing raises it; the result never goes below 0
    assert fee_skills.npc_broker_fee_rate(5, -2.0, 0.0) == pytest.approx(0.0156)
    assert fee_skills.npc_broker_fee_rate(5, 100.0, 100.0) == 0.0


def test_levels_are_clamped_and_missing_rows_are_zero():
    assert fee_skills.sales_tax_rate(7) == pytest.approx(0.03375)
    levels = fee_skills.skill_levels([{"skill_id": 3446, "active_level": 4}])
    assert levels.get(fee_skills.SKILL_ACCOUNTING, 0) == 0
    rows = [{"from_type": "faction", "from_id": 500001, "standing": 2.5},
            {"from_type": "npc_corp", "from_id": 500001, "standing": 9.0}]
    assert fee_skills.standing_from(rows, "faction", 500001) == 2.5
    assert fee_skills.standing_from(rows, "faction", 500002) == 0.0
    assert fee_skills.standing_from(rows, "faction", None) == 0.0


# --------------------------------------------------------------- DB fixtures
pg = pg_helpers.postgres_required()

_TABLES = (
    "esi_sharing", "esi_freshness", "tenant_tokens", "character_skills", "character_slots",
    "character_standings", "prod_cost_layers", "prod_cost_draws", "prod_ledger_runs",
    "ledger_wallet_transactions",
)


@pytest.fixture
def db(tenant):
    pg_helpers.wipe_tables(*_TABLES)
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_npc_corporations (corporation_id, faction_id) VALUES (?, ?) "
            "ON CONFLICT (corporation_id) DO UPDATE SET faction_id = excluded.faction_id",
            (STATION_CORP, STATION_FACTION))
        conn.execute(
            "INSERT INTO sde_stations (station_id, solar_system_id, station_name, corporation_id) "
            "VALUES (?, 30099901, 'Test Station', ?) ON CONFLICT (station_id) DO UPDATE "
            "SET corporation_id = excluded.corporation_id", (STATION, STATION_CORP))
    yield tenant
    pg_helpers.wipe_tables(*_TABLES)


def _share(cid, kind, tool):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING", (cid, kind, tool))


def _token(cid, name, scopes=f"{SKILLS_SCOPE} {STANDINGS_SCOPE}"):
    role = f"esi:{cid}"
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=cid, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=scopes,
    )))


def _skills(cid, accounting=4, broker_relations=5):
    storage.replace_character_skills(cid, [(16622, accounting, accounting, 0),
                                           (3446, broker_relations, broker_relations, 0)])
    storage.upsert_esi_freshness("character", cid, "skills", success=True)


def _standings(cid, faction=0.0, corp=0.0):
    storage.replace_character_standings(cid, [(STATION_FACTION, "faction", faction),
                                              (STATION_CORP, "npc_corp", corp)])
    storage.upsert_esi_freshness("character", cid, "standings", success=True)


def _ready(cid=ALICE, tool="station_trading", *, standings=True):
    _token(cid, "Alice")
    _share(cid, "skills", tool)
    _skills(cid)
    if standings:
        _share(cid, "standings", tool)
        _standings(cid, faction=2.0, corp=5.0)


# ----------------------------------------------------------------- the hints
@pg
def test_no_character_selected_gives_no_value(db):
    out = fee_skill_hints.skill_fee_hints(
        "station_trading", None, location_id=STATION,
        sales_tax_setting="sales_tax_rate", broker_setting="broker_fee_rate")
    assert out["reason"] == "no_character_selected"
    assert out["sales_tax"]["suggested"] is None and out["broker_fee"]["suggested"] is None
    assert out["sales_tax"]["reason"] == "no_character_selected"


@pg
def test_unshared_skills_give_a_reason_not_a_value(db):
    _token(ALICE, "Alice")
    _skills(ALICE)
    _share(ALICE, "skills", "production")       # shared, but with another tool
    out = fee_skill_hints.skill_fee_hints(
        "station_trading", ALICE, location_id=STATION,
        sales_tax_setting="sales_tax_rate", broker_setting="broker_fee_rate")
    assert out["reason"] == "not_shared"
    assert out["sales_tax"]["suggested"] is None
    assert out["broker_fee"]["reason"] == "not_shared"


@pg
def test_shared_without_a_scoped_token_is_reauth_needed(db):
    _token(ALICE, "Alice", scopes="esi-assets.read_assets.v1")
    _share(ALICE, "skills", "pi")
    _skills(ALICE)
    out = fee_skill_hints.skill_fee_hints("pi", ALICE, location_id=STATION,
                                          sales_tax_setting="pi_sales_tax_rate")
    assert out["reason"] == "reauth_needed"
    assert out["sales_tax"]["suggested"] is None


@pg
def test_skills_and_standings_at_an_npc_station(db):
    _ready()
    out = fee_skill_hints.skill_fee_hints(
        "station_trading", ALICE, location_id=STATION,
        sales_tax_setting="sales_tax_rate", broker_setting="broker_fee_rate")
    assert out["reason"] is None and out["character_name"] == "Alice"
    assert out["sales_tax"]["suggested"] == pytest.approx(0.042)
    broker = out["broker_fee"]
    # 3 % - 1.5 % - 0.03 % x 2 - 0.02 % x 5 = 1.34 %
    assert broker["suggested"] == pytest.approx(0.0134)
    assert broker["source"] == "skills_standings" and broker["upper_bound"] is False
    assert broker["standings"]["faction_standing"] == 2.0
    assert broker["standings"]["corporation_standing"] == 5.0


@pg
def test_standings_not_synced_shows_the_skill_value_as_upper_bound(db):
    _ready(standings=False)
    _share(ALICE, "standings", "station_trading")     # shared, never synced
    out = fee_skill_hints.skill_fee_hints(
        "station_trading", ALICE, location_id=STATION, broker_setting="broker_fee_rate")
    broker = out["broker_fee"]
    assert broker["suggested"] == pytest.approx(0.015)
    assert broker["upper_bound"] is True and broker["upper_bound_reason"] == "not_synced"


@pg
def test_unknown_station_owner_is_an_upper_bound(db):
    _ready()
    with storage.connect() as conn:
        conn.execute("UPDATE sde_stations SET corporation_id = NULL WHERE station_id = ?", (STATION,))
    broker = fee_skill_hints.skill_fee_hints(
        "station_trading", ALICE, location_id=STATION, broker_setting="broker_fee_rate")["broker_fee"]
    assert broker["suggested"] == pytest.approx(0.015)
    assert broker["upper_bound_reason"] == "station_owner_unknown"


@pg
def test_player_structure_has_no_standing_effect(db):
    _ready()
    out = fee_skill_hints.skill_fee_hints(
        "station_trading", ALICE, location_id=STRUCTURE,
        sales_tax_setting="sales_tax_rate", broker_setting="broker_fee_rate")
    assert out["location"]["kind"] == "player_structure"
    assert out["sales_tax"]["suggested"] == pytest.approx(0.042)   # sales tax still from skills
    assert out["broker_fee"]["suggested"] is None
    assert out["broker_fee"]["reason"] == "player_structure"


@pg
def test_candidates_are_characters_sharing_skills_with_the_tool(db):
    _token(ALICE, "Alice")
    _token(BOB, "Bob")
    _share(ALICE, "skills", "trading")
    _share(ALICE, "standings", "trading")
    _share(BOB, "skills", "trading")
    _share(BOB, "skills", "pi")
    got = fee_skill_hints.candidates("trading")
    assert got == [
        {"character_id": ALICE, "character_name": "Alice", "shares_standings": True},
        {"character_id": BOB, "character_name": "Bob", "shares_standings": False},
    ]
    assert [c["character_id"] for c in fee_skill_hints.candidates("pi")] == [BOB]


@pg
def test_read_esi_returns_per_skill_rows_for_trading_and_station_trading(db):
    _ready(tool="trading", standings=False)
    _share(ALICE, "skills", "station_trading")
    storage.upsert_character_slot_row("Alice", 5, 2, 3, owner_character_id=ALICE)
    rows = read_esi("skills", "trading", owner_type="character", table="skills")
    assert {r["skill_id"] for r in rows} == {16622, 3446}
    rows = read_esi("skills", "station_trading", owner_type="character", table="skills")
    assert {r["skill_id"] for r in rows} == {16622, 3446}
    slots = read_esi("skills", "station_trading", owner_type="character")
    assert slots[0]["manufacturing_slots"] == 5          # slot rows stay the default


# ------------------------------------------------- per tool, settings saves
@pg
def test_station_trading_selection_changes_no_fee_and_click_saves(db):
    _ready()
    original = {k: getattr(STATION_TRADING_CONFIG, k)
                for k in ("broker_fee_rate", "station_id", "fee_skill_character_id")}
    try:
        apply_config_overrides(STATION_TRADING_CONFIG, {"station_id": STATION})
        station_actions.do_update_settings({"fee_skill_character_id": ALICE})
        assert STATION_TRADING_CONFIG.broker_fee_rate == original["broker_fee_rate"]
        assert "broker_fee_rate" not in storage.load_tenant_settings("station_trading")
        out = station_actions.do_get_fee_suggestions()
        suggested = out["skills"]["broker_fee"]["suggested"]
        assert suggested == pytest.approx(0.0134)
        assert out["observed"]["reason"] == "no_run"
        station_actions.do_update_settings({"broker_fee_rate": suggested})
        assert storage.load_tenant_settings("station_trading")["broker_fee_rate"] == pytest.approx(0.0134)
    finally:
        apply_config_overrides(STATION_TRADING_CONFIG, original)


@pg
def test_pi_hint_uses_its_own_selection_and_price_structure(db):
    _ready(tool="pi")
    original = {k: getattr(PI_CONFIG, k) for k in ("pi_fee_skill_character_id", "pi_price_structure_id")}
    try:
        assert pi_actions.do_get_fee_suggestions()["skills"]["reason"] == "no_character_selected"
        pi_actions.do_update_settings({"pi_fee_skill_character_id": ALICE})
        apply_config_overrides(PI_CONFIG, {"pi_price_structure_id": STRUCTURE})
        out = pi_actions.do_get_fee_suggestions()["skills"]
        assert out["sales_tax"]["setting"] == "pi_sales_tax_rate"
        assert out["sales_tax"]["suggested"] == pytest.approx(0.042)
        assert out["broker_fee"]["reason"] == "player_structure"
    finally:
        apply_config_overrides(PI_CONFIG, original)


# ------------------------------------------------------ observed buy quote
def _tx(n, *, owner=ALICE, location=60003760, days_ago=3, price=1000.0, qty=10):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO ledger_wallet_transactions (owner_type, owner_id, division, transaction_id, "
            "date, type_id, location_id, unit_price, quantity, is_buy, journal_ref_id) "
            "VALUES ('character', ?, 0, ?, ?, 34, ?, ?, ?, TRUE, ?)",
            (owner, n, NOW - timedelta(days=days_ago), location, price, qty, n))
    return f"tx:character:{owner}:0:{n}"


def _layer(ref, layer_id, *, qty=10, left=10, unit_price=1000.0, unit_fee=10.0, source="market"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO prod_cost_layers (type_id, unit, quantity_left, unit_cost, origin, source_ref, "
            "cost_materials_bought, cost_fees, quantity_original, logical_layer_id, source) "
            "VALUES (34, 'item', ?, ?, 'bought', ?, ?, ?, ?, ?, ?)",
            (left, unit_price + unit_fee, ref, unit_price, unit_fee, qty, layer_id, source))


def _draw(ref, layer_id, *, qty=10, unit_price=1000.0, unit_fee=0.0):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO prod_cost_draws (consumer_kind, consumer_ref, seq, logical_layer_id, type_id, "
            "unit, quantity, origin, source_ref, cost_materials_bought, cost_fees, source) "
            "VALUES ('sale', ?, 0, ?, 34, 'item', ?, 'bought', ?, ?, ?, 'market')",
            (f"sale-{layer_id}", layer_id, qty, ref, unit_price * qty, unit_fee * qty))


def _buy_ready():
    _share(ALICE, "wallet", "trading")
    with storage.connect() as conn:
        conn.execute("INSERT INTO prod_ledger_runs (cutover_t0, owner_scope_hash) VALUES (?, ?)",
                     (NOW - timedelta(days=90), access.owner_scope_hash()))


@pg
def test_observed_buy_quote_counts_open_and_consumed_layers_at_the_hub(db):
    _buy_ready()
    hub_locations = [60003760]
    # 10 order buys with a 1 % fee, still in stock (one partly drawn: the layer counts once)
    for n in range(1, 11):
        ref = _tx(n)
        _layer(ref, n, left=5 if n == 1 else 10)
    _draw(f"tx:character:{ALICE}:0:1", 1, qty=5, unit_fee=10.0)
    # 10 instant buys without a fee, fully consumed (only their draws remain)
    for n in range(11, 21):
        _draw(_tx(n), n)
    # outside the hub, another owner, a transfer and an old buy: none count
    _layer(_tx(30, location=60008494), 30, unit_fee=500.0)
    _layer(_tx(31, owner=BOB), 31, unit_fee=500.0)
    _layer(_tx(32), 32, unit_fee=500.0, source="transfer")
    _layer(_tx(33, days_ago=40), 33, unit_fee=500.0)
    out = trade_report.observed_buy_fees(hub_locations)
    assert out["buys"] == 20
    assert out["suggested"] == pytest.approx(0.005)      # 1 % on half the value
    assert out["setting"] == "jita_buy_broker_fee"


@pg
def test_observed_buy_quote_needs_twenty_buys_a_run_and_current_sharing(db):
    _buy_ready()
    for n in range(1, 20):
        _layer(_tx(n), n)
    out = trade_report.observed_buy_fees([60003760])
    assert out["suggested"] is None and out["reason"] == buy_fees.REASON_TOO_FEW_BUYS
    _layer(_tx(20), 20)
    assert trade_report.observed_buy_fees([60003760])["suggested"] == pytest.approx(0.01)
    assert trade_report.observed_buy_fees(None)["reason"] == buy_fees.REASON_NO_HUB
    _share(BOB, "wallet", "trading")
    assert trade_report.observed_buy_fees([60003760])["reason"] == buy_fees.REASON_NEEDS_REBUILD
    pg_helpers.wipe_tables("prod_ledger_runs")
    assert trade_report.observed_buy_fees([60003760])["reason"] == buy_fees.REASON_NO_RUN


@pg
def test_trading_suggestions_and_the_click_go_through_the_normal_save(db):
    _ready(tool="trading")
    original = {k: getattr(TRADING_CONFIG, k)
                for k in ("jita_buy_broker_fee", "fee_skill_character_id", "jita_region_id")}
    try:
        apply_config_overrides(TRADING_CONFIG, {"jita_region_id": 10000002})
        trading_actions.do_update_settings({"fee_skill_character_id": ALICE})
        assert TRADING_CONFIG.jita_buy_broker_fee == original["jita_buy_broker_fee"]
        out = trading_actions.do_get_fee_suggestions()
        assert out["hub"] == "Jita"
        broker = out["skills"]["broker_fee"]
        assert broker["setting"] == "jita_buy_broker_fee" and broker["suggested"] is not None
        assert out["observed_buy"]["reason"] == buy_fees.REASON_NO_RUN
        trading_actions.do_update_settings({"jita_buy_broker_fee": broker["suggested"]})
        assert storage.load_tenant_settings("trading")["jita_buy_broker_fee"] == broker["suggested"]
        with pytest.raises(trading_actions.ActionError):
            trading_actions.do_update_settings({"fee_skill_character_id": "Alice"})
    finally:
        apply_config_overrides(TRADING_CONFIG, original)


# ------------------------------------------------------------------- the SDE
@pg
def test_sde_fetch_and_apply_carry_station_owner_and_corporation_faction(db, monkeypatch):
    from eve_trader.production import sde

    files = {
        "staStations.csv": [{"stationID": "60003760", "solarSystemID": "30000142",
                             "stationName": "Jita IV - Moon 4", "corporationID": "1000035"}],
        "crpNPCCorporations.csv": [{"corporationID": "1000035", "factionID": "500001"},
                                   {"corporationID": "1000001", "factionID": ""}],
    }
    monkeypatch.setattr(sde, "_fetch_csv", lambda session, base, filename: files.get(filename, []))
    monkeypatch.setattr(sde, "_MIN_STREAM_ROWS", {k: 0 for k in sde._MIN_STREAM_ROWS})
    monkeypatch.setattr(sde, "_fetch_attribute_ids", lambda session, base: {})
    monkeypatch.setattr(sde, "_dump_etag", lambda *a, **k: "etag")
    monkeypatch.setattr(sde, "_fetch_skill_attributes", lambda *a, **k: ({}, {}, {}))
    monkeypatch.setattr(sde, "_fetch_pi_planets", lambda session, base: [])
    assert "crpNPCCorporations.csv" in sde._SDE_CSV_FILES
    assert "corporationID" in sde._CSV_SPEC["staStations.csv"][0]
    fetched = sde.fetch_sde()
    assert fetched.stations == [(60003760, 30000142, "Jita IV - Moon 4", 1000035)]
    assert fetched.npc_corporations == [(1000035, 500001), (1000001, None)]
    sde.apply_sde(fetched)
    assert storage.npc_station_owner(60003760) == {
        "station_name": "Jita IV - Moon 4", "corporation_id": 1000035, "faction_id": 500001}
    assert storage.npc_station_owner(60000001) is None

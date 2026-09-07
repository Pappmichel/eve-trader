"""Shared synthetic SDE + planner network stubs for Special-Order phase tests.

SDE tables are global (no tenant_id). Special-order rows are RLS-scoped via
the `tenant` fixture. Import `_apply_special_orders_schema` so events/header
tables exist.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production.config import ProductionConfig
from eve_trader.production.constants import DECRYPTORS

from . import pg_helpers
from .pg_helpers import tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

_DOCS = Path(__file__).resolve().parent.parent / "docs"
_PHASE1_SCHEMA_SQL = _DOCS / "phase1_schema.sql"
_PHASE2_SCHEMA_SQL = _DOCS / "phase2_schema.sql"
_REFINING_SCHEMA_SQL = _DOCS / "refining_schema.sql"
_SPECIAL_ORDERS_SCHEMA_SQL = _DOCS / "special_orders_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_special_orders_schema():
    """Idempotent owner-role apply for a *fresh* Postgres (CI).

    `storage.replace_sde_data` always DELETEs `sde_type_materials` and INSERTs
    9-column `sde_types` rows (`portion_size`). Those live in
    `refining_schema.sql`, which itself ALTERs `tenant_settings` from phase2.
    A Cloud Agent VM that already ran other test modules hid this; GitHub
    Actions starts empty and does not.
    """
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_PHASE1_SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute(_PHASE2_SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute(_REFINING_SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute(_SPECIAL_ORDERS_SCHEMA_SQL.read_text(encoding="utf-8"))

MINERAL = 34
COMPONENT = 91201
FINISHED_A = 91202
FINISHED_B = 91203
COMPONENT_BP, FINISHED_A_BP, FINISHED_B_BP = 92201, 92202, 92203

T1_BLUEPRINT = 1002
T2_BLUEPRINT = 1001
T2_MODULE = 2048
T1_BLUEPRINT_B = 1004
T2_BLUEPRINT_B = 1003
T2_MODULE_B = 2050
DATACORE_A = 20410
DATACORE_B = 20424
TRITANIUM = 34
MORPHITE = 11399
BASE_PROBABILITY = 0.34
BASE_RUNS = 10


def widget_cfg(**overrides) -> ProductionConfig:
    cfg = ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                           facility_tax_rate=0.0, market_fees=0.0,
                           component_overbuild=0.0, min_margin=0.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def invention_cfg(**overrides) -> ProductionConfig:
    cfg = widget_cfg(encryption_skill_level=4, datacore_skill_1_level=4,
                     datacore_skill_2_level=4)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


HOME = {MINERAL: CurrentPrice(type_id=MINERAL, updated="", buy=4.5, sell=5.0)}

DECRYPTOR_PRICES = {
    "Accelerant": 500_000.0, "Attainment": 400_000.0, "Augmentation": 1_000_000.0,
    "Parity": 600_000.0, "Process": 300_000.0, "Symmetry": 450_000.0,
    "Optimized Attainment": 900_000.0, "Optimized Augmentation": 1_200_000.0,
}

JITA = {
    DATACORE_A: CurrentPrice(type_id=DATACORE_A, updated="", buy=0.0, sell=1000.0),
    DATACORE_B: CurrentPrice(type_id=DATACORE_B, updated="", buy=0.0, sell=2000.0),
    TRITANIUM: CurrentPrice(type_id=TRITANIUM, updated="", buy=0.0, sell=5.0),
    MORPHITE: CurrentPrice(type_id=MORPHITE, updated="", buy=0.0, sell=10000.0),
}
JITA.update({
    d.type_id: CurrentPrice(type_id=d.type_id, updated="", buy=0.0, sell=price)
    for name, price in DECRYPTOR_PRICES.items()
    for d in [DECRYPTORS[name]] if d.type_id
})


def seed_widgets() -> None:
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (COMPONENT, 18, "Widget Component", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_A, 18, "Finished Widget A", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_B, 18, "Finished Widget B", 1.0, 1, 200, 0, 1, 1),
            (COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_A_BP, 9, "Finished Widget A Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_B_BP, 9, "Finished Widget B Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[],
        blueprint_materials=[
            (COMPONENT_BP, 1, MINERAL, 10),
            (FINISHED_A_BP, 1, COMPONENT, 2),
            (FINISHED_B_BP, 1, COMPONENT, 3),
        ],
        blueprint_products=[
            (COMPONENT_BP, 1, COMPONENT, 1),
            (FINISHED_A_BP, 1, FINISHED_A, 1),
            (FINISHED_B_BP, 1, FINISHED_B, 1),
        ],
        categories=[(4, "Material")],
    )


def seed_invention() -> None:
    storage.replace_sde_data(
        types=[
            (TRITANIUM, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (MORPHITE, 18, "Morphite", 0.01, 1, 100, 0, 1, 1),
            (DATACORE_A, 333, "Datacore - Mechanical Engineering", 0.1, 1, 100, 0, None, 1),
            (DATACORE_B, 333, "Datacore - Molecular Engineering", 0.1, 1, 100, 0, None, 1),
            (T2_MODULE, 60, "Damage Control II", 5.0, 1, 200, 5, 2, 1),
            (T2_MODULE_B, 60, "Capacitor Power Relay II", 5.0, 1, 200, 5, 2, 1),
            (T1_BLUEPRINT, 9, "Damage Control I Blueprint", 0.01, 1, None, 0, None, 1),
            (T2_BLUEPRINT, 9, "Damage Control II Blueprint", 0.01, 1, None, 0, None, 1),
            (T1_BLUEPRINT_B, 9, "Capacitor Power Relay I Blueprint", 0.01, 1, None, 0, None, 1),
            (T2_BLUEPRINT_B, 9, "Capacitor Power Relay II Blueprint", 0.01, 1, None, 0, None, 1),
        ] + [(d.type_id, 314, name, 0.1, 1, 100, 0, None, 1)
             for name, d in DECRYPTORS.items() if d.type_id],
        groups=[(18, 4, "Mineral"), (60, 7, "Damage Control"), (9, 9, "Blueprint"),
                (333, 4, "Datacores"), (314, 4, "Decryptors")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[(T1_BLUEPRINT, 8, 3600.0), (T1_BLUEPRINT_B, 8, 3600.0)],
        blueprint_materials=[
            (T1_BLUEPRINT, 8, DATACORE_A, 2),
            (T1_BLUEPRINT, 8, DATACORE_B, 2),
            (T2_BLUEPRINT, 1, TRITANIUM, 1000),
            (T2_BLUEPRINT, 1, MORPHITE, 1),
            (T1_BLUEPRINT_B, 8, DATACORE_A, 2),
            (T1_BLUEPRINT_B, 8, DATACORE_B, 2),
            (T2_BLUEPRINT_B, 1, TRITANIUM, 1000),
            (T2_BLUEPRINT_B, 1, MORPHITE, 1),
        ],
        blueprint_products=[
            (T2_BLUEPRINT, 1, T2_MODULE, 1),
            (T1_BLUEPRINT, 8, T2_BLUEPRINT, BASE_RUNS),
            (T2_BLUEPRINT_B, 1, T2_MODULE_B, 1),
            (T1_BLUEPRINT_B, 8, T2_BLUEPRINT_B, BASE_RUNS),
        ],
        invention_probability=[
            (T1_BLUEPRINT, T2_BLUEPRINT, BASE_PROBABILITY),
            (T1_BLUEPRINT_B, T2_BLUEPRINT_B, BASE_PROBABILITY),
        ],
        categories=[(4, "Material"), (7, "Module"), (9, "Blueprint")],
    )


def patch_planner_network(monkeypatch, home=None, jita=None) -> None:
    from eve_trader.production import engine, pricing
    from eve_trader import esi_client as esi_client_module

    home_map = HOME if home is None else home
    jita_map = {} if jita is None else jita
    monkeypatch.setattr(engine.pricing, "home_prices", lambda cfg, type_ids: home_map)
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids: jita_map)
    monkeypatch.setattr(pricing, "system_cost_indices_for", lambda *a, **k: {})

    class _FakeESIClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_adjusted_prices(self):
            return {}

    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)


def fingerprint(plan: dict) -> dict:
    return {
        "line": frozenset((r.type_id, r.quantity) for r in plan["line_items"]),
        "build": frozenset((r.type_id, r.job_runs, r.quantity) for r in plan["build_list"]),
        "buy": frozenset((r.type_id, r.quantity) for r in plan["buy_list"]),
        "inv": frozenset(
            (r.type_id, r.runs_needed, r.recommended_invention_runs, r.decryptor)
            for r in plan["invention_list"]
        ),
        "overlap": frozenset((r.type_id, r.current_stock) for r in plan["stock_overlap_warning"]),
    }


def runs(plan: dict) -> dict[int, int]:
    return {row.type_id: row.job_runs for row in plan["build_list"]}


def buy_qty(plan: dict) -> dict[int, float]:
    return {row.type_id: row.quantity for row in plan["buy_list"]}


def lines(plan: dict) -> dict[int, float]:
    return {row.type_id: row.quantity for row in plan["line_items"]}


def persistent_state() -> dict:
    orders = storage.list_special_orders()
    return {
        "orders": [(str(r[0]), r[1], r[2], r[3]) for r in orders],
        "items": {str(row[0]): storage.list_special_order_items(str(row[0])) for row in orders},
        "stock_targets": storage.load_stock_targets(),
        "manual_stock": storage.load_manual_stock(),
        "decryptors": storage.load_selected_decryptors(),
        "esi_finished_a": storage.esi_stock_at_location(FINISHED_A, None),
        "esi_component": storage.esi_stock_at_location(COMPONENT, None),
        "esi_t2": storage.esi_stock_at_location(T2_MODULE, None),
    }

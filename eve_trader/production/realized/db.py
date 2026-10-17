"""Postgres helpers for the Production ledger archive.

Every query goes through `storage.connect()` (RLS). Upserts are idempotent
by natural key and never DELETE on resync (plan Phase 1).
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from psycopg.types.json import Jsonb

from ... import storage

# The Forge (Jita's region) - freight is applied only for buys here (D9).
THE_FORGE_REGION_ID = 10000002


def region_id_for_location(location_id: int) -> Optional[int]:
    """Resolve an ESI location_id to a region_id.

    NPC stations: sde_stations -> sde_solar_systems.
    Player structures: this tenant's structure_names, then global_structure_names.
    Unresolved -> None (caller records a warning / leaves region_id NULL).
    """
    loc = int(location_id)
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT sys.region_id FROM sde_stations st "
            "JOIN sde_solar_systems sys ON sys.solar_system_id = st.solar_system_id "
            "WHERE st.station_id = ?",
            (loc,),
        ).fetchone()
        if row is not None:
            return int(row[0])
        row = conn.execute(
            "SELECT solar_system_id FROM structure_names WHERE location_id = ?",
            (loc,),
        ).fetchone()
    system_id = int(row[0]) if row and row[0] is not None else None
    if system_id is None:
        global_hit = storage.get_global_structure_names([loc]).get(loc)
        if global_hit is not None and global_hit[1] is not None:
            system_id = int(global_hit[1])
    if system_id is None:
        return None
    system = storage.get_solar_system(system_id)
    return int(system[3]) if system is not None else None


def upsert_ledger_wallet_transactions(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, division, transaction_id, date, type_id,
    location_id, unit_price, quantity, is_buy, journal_ref_id, region_id,
    freight_rate_per_m3, freight_m3). Insert-only on conflict (frozen freight)."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_wallet_transactions ("
            "owner_type, owner_id, division, transaction_id, date, type_id, "
            "location_id, unit_price, quantity, is_buy, journal_ref_id, "
            "region_id, freight_rate_per_m3, freight_m3) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, division, transaction_id) "
            "DO NOTHING",
            rows,
        )
    return len(rows)


def upsert_ledger_wallet_journal(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, division, journal_id, date, ref_type,
    amount, context_id, context_id_type, first_party_id, second_party_id,
    description, tax, tax_receiver_id)."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_wallet_journal ("
            "owner_type, owner_id, division, journal_id, date, ref_type, amount, "
            "context_id, context_id_type, first_party_id, second_party_id, "
            "description, tax, tax_receiver_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, division, journal_id) "
            "DO NOTHING",
            rows,
        )
    return len(rows)


def upsert_ledger_market_orders(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, order_id, type_id, location_id, region_id,
    is_buy_order, price, volume_total, volume_remain, issued, state).

    On conflict updates mutable fields (price/volume/state/issued) so a later
    history page still refreshes the last-known order shape without deleting.
    """
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_market_orders ("
            "owner_type, owner_id, order_id, type_id, location_id, region_id, "
            "is_buy_order, price, volume_total, volume_remain, issued, state) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, order_id) DO UPDATE SET "
            "type_id=EXCLUDED.type_id, location_id=EXCLUDED.location_id, "
            "region_id=COALESCE(EXCLUDED.region_id, ledger_market_orders.region_id), "
            "is_buy_order=EXCLUDED.is_buy_order, price=EXCLUDED.price, "
            "volume_total=EXCLUDED.volume_total, volume_remain=EXCLUDED.volume_remain, "
            "issued=EXCLUDED.issued, state=EXCLUDED.state",
            rows,
        )
    return len(rows)


def upsert_ledger_industry_jobs(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, job_id, activity_id, blueprint_id,
    blueprint_type_id, product_type_id, runs, licensed_runs, successful_runs,
    cost, facility_id, station_id, output_location_id, installer_id,
    start_date, end_date, completed_date, status, probability).

    On conflict updates status/completion fields; identity fields stay.
    """
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_industry_jobs ("
            "owner_type, owner_id, job_id, activity_id, blueprint_id, "
            "blueprint_type_id, product_type_id, runs, licensed_runs, "
            "successful_runs, cost, facility_id, station_id, output_location_id, "
            "installer_id, start_date, end_date, completed_date, status, probability) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, job_id) DO UPDATE SET "
            "successful_runs=COALESCE(EXCLUDED.successful_runs, ledger_industry_jobs.successful_runs), "
            "completed_date=COALESCE(EXCLUDED.completed_date, ledger_industry_jobs.completed_date), "
            "status=COALESCE(EXCLUDED.status, ledger_industry_jobs.status), "
            "cost=COALESCE(EXCLUDED.cost, ledger_industry_jobs.cost), "
            "probability=COALESCE(EXCLUDED.probability, ledger_industry_jobs.probability), "
            "end_date=COALESCE(EXCLUDED.end_date, ledger_industry_jobs.end_date)",
            rows,
        )
    return len(rows)


def job_ids_already_archived(
    owner_type: str, owner_id: int, job_ids: Iterable[int],
) -> set[int]:
    ids = [int(j) for j in job_ids]
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    with storage.connect() as conn:
        rows = conn.execute(
            f"SELECT job_id FROM ledger_industry_jobs "
            f"WHERE owner_type = ? AND owner_id = ? AND job_id IN ({placeholders})",
            (owner_type, int(owner_id), *ids),
        ).fetchall()
    return {int(r[0]) for r in rows}


def insert_ledger_job_inputs(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, job_id, type_id, quantity, blueprint_me,
    material_multiplier, decryptor_type_id, encryption_skill_level,
    science_skill_1_level, science_skill_2_level, source).

    Insert-only: frozen at first sight.
    """
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_job_inputs ("
            "owner_type, owner_id, job_id, type_id, quantity, blueprint_me, "
            "material_multiplier, decryptor_type_id, encryption_skill_level, "
            "science_skill_1_level, science_skill_2_level, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, job_id, type_id) DO NOTHING",
            rows,
        )
    return len(rows)


def upsert_ledger_blueprints(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, item_id, type_id, location_id,
    material_efficiency, time_efficiency, runs, quantity)."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_blueprints ("
            "owner_type, owner_id, item_id, type_id, location_id, "
            "material_efficiency, time_efficiency, runs, quantity, last_seen_at) "
            "VALUES (?,?,?,?,?,?,?,?,?, now()) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, item_id) DO UPDATE SET "
            "type_id=EXCLUDED.type_id, location_id=EXCLUDED.location_id, "
            "material_efficiency=EXCLUDED.material_efficiency, "
            "time_efficiency=EXCLUDED.time_efficiency, runs=EXCLUDED.runs, "
            "quantity=EXCLUDED.quantity, last_seen_at=now()",
            rows,
        )
    return len(rows)


def upsert_ledger_contracts(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, contract_id, issuer_id,
    issuer_corporation_id, acceptor_id, start_location_id, end_location_id,
    type, status, title, price, reward, date_issued, date_completed,
    date_expired)."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_contracts ("
            "owner_type, owner_id, contract_id, issuer_id, issuer_corporation_id, "
            "acceptor_id, start_location_id, end_location_id, type, status, title, "
            "price, reward, date_issued, date_completed, date_expired) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, contract_id) DO UPDATE SET "
            "status=EXCLUDED.status, acceptor_id=EXCLUDED.acceptor_id, "
            "date_completed=COALESCE(EXCLUDED.date_completed, ledger_contracts.date_completed), "
            "price=COALESCE(EXCLUDED.price, ledger_contracts.price), "
            "reward=COALESCE(EXCLUDED.reward, ledger_contracts.reward)",
            rows,
        )
    return len(rows)


def upsert_ledger_contract_items(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, contract_id, record_id, type_id,
    quantity, is_included, is_singleton). Insert-only."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_contract_items ("
            "owner_type, owner_id, contract_id, record_id, type_id, quantity, "
            "is_included, is_singleton) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, contract_id, record_id) "
            "DO NOTHING",
            rows,
        )
    return len(rows)


def newest_archived_date(
    table: str, owner_type: str, owner_id: int, date_column: str,
    division: Optional[int] = None,
) -> Optional[datetime]:
    """Newest archived timestamp for gap detection (NULL if archive empty).
    `division` narrows the wallet tables to one wallet division."""
    allowed = {
        "ledger_wallet_transactions": "date",
        "ledger_wallet_journal": "date",
        "ledger_industry_jobs": "start_date",
        "ledger_market_orders": "issued",
        "ledger_contracts": "date_issued",
    }
    if table not in allowed or date_column != allowed[table]:
        raise ValueError(f"unsupported gap table/column {table}.{date_column}")
    sql = f"SELECT MAX({date_column}) FROM {table} WHERE owner_type = ? AND owner_id = ?"
    params: list = [owner_type, int(owner_id)]
    if division is not None:
        if table not in ("ledger_wallet_transactions", "ledger_wallet_journal"):
            raise ValueError(f"{table} has no wallet division")
        sql += " AND division = ?"
        params.append(int(division))
    with storage.connect() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return row[0] if row and row[0] is not None else None


def last_archive_sync(
    owner_type: str, owner_id: int, data_kind: str, division: int = 0,
) -> Optional[datetime]:
    """When this (owner, kind, division) was last archived, or None."""
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT last_synced_at FROM ledger_sync_state "
            "WHERE owner_type = ? AND owner_id = ? AND data_kind = ? AND division = ?",
            (owner_type, int(owner_id), data_kind, int(division)),
        ).fetchone()
    return row[0] if row else None


def mark_archive_synced(
    owner_type: str, owner_id: int, data_kind: str, synced_at: datetime,
    division: int = 0,
) -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO ledger_sync_state (owner_type, owner_id, data_kind, division, last_synced_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, data_kind, division) "
            "DO UPDATE SET last_synced_at = EXCLUDED.last_synced_at",
            (owner_type, int(owner_id), data_kind, int(division), synced_at),
        )


def archived_transaction_keys(
    owner_type: str, owner_id: int, transaction_ids: Iterable[int],
) -> set[tuple[int, int]]:
    """(division, transaction_id) pairs already archived for this owner -
    one query, so a sync only processes rows that are new to the archive."""
    ids = sorted({int(t) for t in transaction_ids})
    if not ids:
        return set()
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT division, transaction_id FROM ledger_wallet_transactions "
            "WHERE owner_type = ? AND owner_id = ? AND transaction_id = ANY(?)",
            (owner_type, int(owner_id), ids),
        ).fetchall()
    return {(int(r[0]), int(r[1])) for r in rows}


def transactions_missing_region(owner_type: str, owner_id: int) -> list[tuple]:
    """(division, transaction_id, location_id, type_id, quantity, is_buy) of
    archived rows whose region could not be resolved yet."""
    with storage.connect() as conn:
        return conn.execute(
            "SELECT division, transaction_id, location_id, type_id, quantity, is_buy "
            "FROM ledger_wallet_transactions "
            "WHERE owner_type = ? AND owner_id = ? AND region_id IS NULL",
            (owner_type, int(owner_id)),
        ).fetchall()


def set_transaction_region(rows: list[tuple]) -> int:
    """`rows`: (region_id, freight_rate_per_m3, freight_m3, owner_type,
    owner_id, division, transaction_id). Only fills rows whose region is
    still NULL, so a frozen value is never overwritten."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "UPDATE ledger_wallet_transactions SET region_id = ?, "
            "freight_rate_per_m3 = ?, freight_m3 = ? "
            "WHERE owner_type = ? AND owner_id = ? AND division = ? "
            "AND transaction_id = ? AND region_id IS NULL",
            rows,
        )
    return len(rows)


def job_ids_with_inputs(
    owner_type: str, owner_id: int, job_ids: Iterable[int],
) -> set[int]:
    ids = sorted({int(j) for j in job_ids})
    if not ids:
        return set()
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT job_id FROM ledger_job_inputs "
            "WHERE owner_type = ? AND owner_id = ? AND job_id = ANY(?)",
            (owner_type, int(owner_id), ids),
        ).fetchall()
    return {int(r[0]) for r in rows}


def contract_ids_with_items(
    owner_type: str, owner_id: int, contract_ids: Iterable[int],
) -> set[int]:
    ids = sorted({int(c) for c in contract_ids})
    if not ids:
        return set()
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT contract_id FROM ledger_contract_items "
            "WHERE owner_type = ? AND owner_id = ? AND contract_id = ANY(?)",
            (owner_type, int(owner_id), ids),
        ).fetchall()
    return {int(r[0]) for r in rows}


def manufactured_product_of(blueprint_type_id: int) -> Optional[int]:
    """The item a blueprint manufactures (activity 1), e.g. the T2 module of
    an invented T2 BPC - `selected_decryptors` is keyed by that item."""
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT product_type_id FROM sde_blueprint_products "
            "WHERE blueprint_type_id = ? AND activity_id = 1 "
            "ORDER BY product_type_id LIMIT 1",
            (int(blueprint_type_id),),
        ).fetchone()
    return int(row[0]) if row else None


def record_archive_gap(
    owner_type: str,
    owner_id: int,
    data_kind: str,
    gap_start: datetime,
    gap_end: datetime,
) -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO ledger_archive_gaps ("
            "owner_type, owner_id, data_kind, gap_start, gap_end) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, data_kind, gap_start, gap_end) "
            "DO NOTHING",
            (owner_type, int(owner_id), data_kind, gap_start, gap_end),
        )


def count_ledger_rows(table: str, owner_type: str, owner_id: int) -> int:
    allowed = {
        "ledger_wallet_transactions", "ledger_wallet_journal",
        "ledger_industry_jobs", "ledger_market_orders", "ledger_contracts",
        "ledger_contract_items", "ledger_blueprints", "ledger_job_inputs",
        "ledger_archive_gaps",
    }
    if table not in allowed:
        raise ValueError(f"unsupported ledger table {table}")
    with storage.connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE owner_type = ? AND owner_id = ?",
            (owner_type, int(owner_id)),
        ).fetchone()
    return int(row[0]) if row else 0


def list_ledger_wallet_transactions(
    owner_type: str, owner_id: int,
) -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, division, transaction_id, date, type_id, "
            "location_id, unit_price, quantity, is_buy, journal_ref_id, region_id, "
            "freight_rate_per_m3, freight_m3 "
            "FROM ledger_wallet_transactions "
            "WHERE owner_type = ? AND owner_id = ? "
            "ORDER BY transaction_id",
            (owner_type, int(owner_id)),
        ).fetchall()


def list_ledger_industry_jobs(owner_type: str, owner_id: int) -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, job_id, activity_id, blueprint_id, "
            "blueprint_type_id, product_type_id, runs, status, cost, probability "
            "FROM ledger_industry_jobs "
            "WHERE owner_type = ? AND owner_id = ? ORDER BY job_id",
            (owner_type, int(owner_id)),
        ).fetchall()


def list_ledger_job_inputs(
    owner_type: str, owner_id: int, job_id: int,
) -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT type_id, quantity, blueprint_me, material_multiplier, "
            "decryptor_type_id, source "
            "FROM ledger_job_inputs "
            "WHERE owner_type = ? AND owner_id = ? AND job_id = ? "
            "ORDER BY type_id",
            (owner_type, int(owner_id), int(job_id)),
        ).fetchall()


def list_archive_gaps(owner_type: str, owner_id: int) -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT data_kind, gap_start, gap_end, detected_at "
            "FROM ledger_archive_gaps "
            "WHERE owner_type = ? AND owner_id = ? "
            "ORDER BY gap_start",
            (owner_type, int(owner_id)),
        ).fetchall()


def lookup_blueprint_me_te(item_id: int) -> Optional[tuple[int, int, str]]:
    """ME/TE for a blueprint item_id: live snapshot first, then ledger.

    Returns (me, te, source) with source in
    {esi_blueprint, archived_blueprint}, or None.
    """
    iid = int(item_id)
    with storage.connect() as conn:
        for table, source in (
            ("character_blueprints", "esi_blueprint"),
            ("corp_blueprints", "esi_blueprint"),
        ):
            row = conn.execute(
                f"SELECT material_efficiency, time_efficiency FROM {table} "
                "WHERE item_id = ?",
                (iid,),
            ).fetchone()
            if row is not None:
                return int(row[0]), int(row[1]), source
        row = conn.execute(
            "SELECT material_efficiency, time_efficiency FROM ledger_blueprints "
            "WHERE item_id = ? ORDER BY last_seen_at DESC LIMIT 1",
            (iid,),
        ).fetchone()
    if row is not None:
        return int(row[0]), int(row[1]), "archived_blueprint"
    return None


def lookup_manual_blueprint_me(blueprint_type_id: int) -> Optional[tuple[int, int]]:
    """Best ME/TE across manual originals of this type, else None."""
    return storage.manual_bpo_best_me_te(int(blueprint_type_id))


# ====================================================================== Phase 2 bulk reads / derived writes
# Owners are (owner_type, owner_id) pairs already gated by access.py.


def _owner_in_clause(owners: list[tuple[str, int]]) -> tuple[str, list]:
    if not owners:
        return "FALSE", []
    parts = []
    params: list = []
    for ot, oid in owners:
        parts.append("(owner_type = ? AND owner_id = ?)")
        params.extend([ot, int(oid)])
    return "(" + " OR ".join(parts) + ")", params


def list_wallet_transactions_for_owners(
    owners: list[tuple[str, int]],
) -> list[tuple]:
    """All archived wallet txs for the given owners (one query)."""
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, division, transaction_id, date, type_id, "
            "location_id, unit_price, quantity, is_buy, journal_ref_id, region_id, "
            "freight_rate_per_m3, freight_m3 "
            "FROM ledger_wallet_transactions "
            f"WHERE {where} ORDER BY date, transaction_id",
            tuple(params),
        ).fetchall()


def list_wallet_journal_for_owners(
    owners: list[tuple[str, int]],
) -> list[tuple]:
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, division, journal_id, date, ref_type, amount, "
            "context_id, context_id_type "
            "FROM ledger_wallet_journal "
            f"WHERE {where} ORDER BY date, journal_id",
            tuple(params),
        ).fetchall()


def list_market_orders_for_owners(
    owners: list[tuple[str, int]],
) -> list[tuple]:
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, order_id, type_id, location_id, "
            "is_buy_order, price, volume_total, issued, state "
            "FROM ledger_market_orders "
            f"WHERE {where} ORDER BY issued, order_id",
            tuple(params),
        ).fetchall()


def list_industry_jobs_for_owners(
    owners: list[tuple[str, int]],
) -> list[tuple]:
    """Full job rows needed by costing (dates, runs, cost, installer, …)."""
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, job_id, activity_id, blueprint_id, "
            "blueprint_type_id, product_type_id, runs, licensed_runs, "
            "successful_runs, cost, facility_id, installer_id, "
            "start_date, end_date, completed_date, status, probability "
            "FROM ledger_industry_jobs "
            f"WHERE {where} ORDER BY start_date, job_id",
            tuple(params),
        ).fetchall()


def list_job_inputs_for_owners(
    owners: list[tuple[str, int]],
) -> list[tuple]:
    """All frozen inputs for the owners' jobs: (owner_type, owner_id, job_id,
    type_id, quantity, source)."""
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, job_id, type_id, quantity, source "
            "FROM ledger_job_inputs "
            f"WHERE {where} ORDER BY job_id, type_id",
            tuple(params),
        ).fetchall()


def oldest_wallet_transaction_date(
    owners: list[tuple[str, int]],
) -> Optional[datetime]:
    """Cutover T0 candidate: oldest archived wallet tx among these owners."""
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        row = conn.execute(
            f"SELECT MIN(date) FROM ledger_wallet_transactions WHERE {where}",
            tuple(params),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def list_cached_reference_prices(
    type_ids: list[int],
) -> dict[tuple[int, object], tuple[float, str]]:
    """{(type_id, price_date): (avg_price, source)} for the given types."""
    if not type_ids:
        return {}
    unique = list(dict.fromkeys(int(t) for t in type_ids))
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT type_id, price_date, avg_price, source "
            "FROM ledger_reference_prices WHERE type_id = ANY(?)",
            (unique,),
        ).fetchall()
    return {(int(r[0]), r[1]): (float(r[2]), str(r[3])) for r in rows}


def upsert_reference_prices(rows: list[tuple]) -> int:
    """`rows`: (type_id, price_date, avg_price, source). Insert-only freeze."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_reference_prices (type_id, price_date, avg_price, source) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT (tenant_id, type_id, price_date) DO NOTHING",
            rows,
        )
    return len(rows)


def list_opening_cost_overrides() -> dict[int, float]:
    """Manual opening unit-cost overrides: type_id -> amount (ISK/unit)."""
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT type_id, amount FROM ledger_manual_entries "
            "WHERE entry_kind = 'opening_cost' AND type_id IS NOT NULL",
        ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows if r[0] is not None}


def replace_derived_costing(
    *,
    layers: list[tuple],
    results: list[tuple],
    cutover_t0: Optional[datetime],
    warnings: list,
) -> int:
    """Fully replace derived costing tables for this tenant (one rebuild).

    `layers`: (type_id, unit, quantity_left, unit_cost, origin, source_ref,
    cost_materials_bought, cost_materials_estimated, cost_freight, cost_job,
    cost_invention, cost_fees, layer_date, job_id)
    `results`: (sale_ref, type_id, quantity, revenue, cost, profit,
    coverage_share, sale_date)
    Returns the new prod_ledger_runs.run_id.
    """
    with storage.connect() as conn:
        conn.execute("DELETE FROM prod_cost_layers")
        conn.execute("DELETE FROM prod_realized_results")
        if layers:
            conn.executemany(
                "INSERT INTO prod_cost_layers ("
                "type_id, unit, quantity_left, unit_cost, origin, source_ref, "
                "cost_materials_bought, cost_materials_estimated, cost_freight, "
                "cost_job, cost_invention, cost_fees, layer_date, job_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                layers,
            )
        if results:
            conn.executemany(
                "INSERT INTO prod_realized_results ("
                "sale_ref, type_id, quantity, revenue, cost, profit, "
                "coverage_share, sale_date) "
                "VALUES (?,?,?,?,?,?,?,?)",
                results,
            )
        row = conn.execute(
            "INSERT INTO prod_ledger_runs (cutover_t0, warnings) "
            "VALUES (?, ?) RETURNING run_id",
            (cutover_t0, Jsonb(list(warnings))),
        ).fetchone()
    return int(row[0])


def latest_ledger_run() -> Optional[tuple]:
    """(run_id, ran_at, cutover_t0, warnings) of the newest rebuild, or None."""
    with storage.connect() as conn:
        return conn.execute(
            "SELECT run_id, ran_at, cutover_t0, warnings "
            "FROM prod_ledger_runs ORDER BY run_id DESC LIMIT 1",
        ).fetchone()

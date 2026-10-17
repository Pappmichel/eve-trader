"""Postgres helpers for the Production ledger archive.

Every query goes through `storage.connect()` (RLS). Upserts are idempotent
by natural key and never DELETE on resync (plan Phase 1).
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from psycopg.types.json import Jsonb

from .. import storage

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
    science_skill_1_level, science_skill_2_level, source,
    structure_bonus_applied).

    Insert-only: frozen at first sight. `structure_bonus_applied` may be
    None (unknown — a row frozen before that fact was stored).
    """
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_job_inputs ("
            "owner_type, owner_id, job_id, type_id, quantity, blueprint_me, "
            "material_multiplier, decryptor_type_id, encryption_skill_level, "
            "science_skill_1_level, science_skill_2_level, source, "
            "structure_bonus_applied) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
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
    date_expired[, volume, collateral, region_id]).

    A 16-field tuple leaves volume, collateral and region_id NULL and does
    not wipe values already stored.
    """
    if not rows:
        return 0
    padded = []
    for row in rows:
        if len(row) < 16:
            raise ValueError("ledger contract row is missing required fields")
        extra = tuple(row[16:19])
        padded.append(tuple(row[:16]) + extra + (None,) * (3 - len(extra)))
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_contracts ("
            "owner_type, owner_id, contract_id, issuer_id, issuer_corporation_id, "
            "acceptor_id, start_location_id, end_location_id, type, status, title, "
            "price, reward, date_issued, date_completed, date_expired, "
            "volume, collateral, region_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, contract_id) DO UPDATE SET "
            "status=EXCLUDED.status, acceptor_id=EXCLUDED.acceptor_id, "
            "date_completed=COALESCE(EXCLUDED.date_completed, ledger_contracts.date_completed), "
            "price=COALESCE(EXCLUDED.price, ledger_contracts.price), "
            "reward=COALESCE(EXCLUDED.reward, ledger_contracts.reward), "
            "volume=COALESCE(EXCLUDED.volume, ledger_contracts.volume), "
            "collateral=COALESCE(EXCLUDED.collateral, ledger_contracts.collateral), "
            "region_id=COALESCE(EXCLUDED.region_id, ledger_contracts.region_id)",
            padded,
        )
    return len(rows)


def upsert_ledger_contract_items(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, contract_id, record_id, type_id,
    quantity, is_included, is_singleton[, freight_rate_per_m3, freight_m3,
    freight_frozen]). Insert-only. An 8-field tuple leaves freight unfrozen."""
    if not rows:
        return 0
    padded = []
    for row in rows:
        extra = tuple(row[8:11])
        if len(extra) < 3:
            extra = extra + (None,) * (2 - len(extra)) + (False,)
        padded.append(tuple(row[:8]) + extra)
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_contract_items ("
            "owner_type, owner_id, contract_id, record_id, type_id, quantity, "
            "is_included, is_singleton, freight_rate_per_m3, freight_m3, freight_frozen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, contract_id, record_id) "
            "DO NOTHING",
            padded,
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


def wallet_window_starts(owners: list[tuple[str, int]]) -> list[tuple]:
    """Per (owner_type, owner_id, division): (oldest archived transaction,
    oldest archived journal entry or None). One query."""
    if not owners:
        return []
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT t.owner_type, t.owner_id, t.division, MIN(t.date), ("
            "  SELECT MIN(j.date) FROM ledger_wallet_journal j "
            "  WHERE j.owner_type = t.owner_type AND j.owner_id = t.owner_id "
            "  AND j.division = t.division) "
            f"FROM ledger_wallet_transactions t WHERE {where} "
            "GROUP BY t.owner_type, t.owner_id, t.division",
            tuple(params),
        ).fetchall()


def industry_job_ids_present(
    owner_type: str, owner_id: int, job_ids: Iterable[int],
) -> set[int]:
    """Job ids already archived for this owner. A planned-cost freeze only
    runs for ids that are not in this set."""
    ids = sorted({int(j) for j in job_ids})
    if not ids:
        return set()
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT job_id FROM ledger_industry_jobs "
            "WHERE owner_type = ? AND owner_id = ? AND job_id = ANY(?)",
            (owner_type, int(owner_id), ids),
        ).fetchall()
    return {int(r[0]) for r in rows}


def insert_planned_costs(rows: list[tuple]) -> int:
    """`rows`: (owner_type, owner_id, job_id, product_type_id,
    planned_unit_cost, source). Insert-only: the first archive wins."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_job_planned_costs ("
            "owner_type, owner_id, job_id, product_type_id, planned_unit_cost, source) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (tenant_id, owner_type, owner_id, job_id) DO NOTHING",
            rows,
        )
    return len(rows)


def contract_titles(contract_ids: list[int]) -> dict[int, str]:
    """First non-empty title stored for each contract id."""
    ids = [int(i) for i in contract_ids]
    if not ids:
        return {}
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT contract_id, title FROM ledger_contracts "
            "WHERE contract_id = ANY(?) AND title IS NOT NULL AND title <> ''",
            (ids,),
        ).fetchall()
    out: dict[int, str] = {}
    for contract_id, title in rows:
        out.setdefault(int(contract_id), str(title))
    return out


def list_contracts_for_owners(owners: list[tuple[str, int]]) -> list[tuple]:
    """(owner_type, owner_id, contract_id, issuer_id, issuer_corporation_id,
    acceptor_id, start_location_id, end_location_id, type, status, price,
    reward, date_completed, volume, collateral, region_id)."""
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, contract_id, issuer_id, issuer_corporation_id, "
            "acceptor_id, start_location_id, end_location_id, type, status, price, reward, "
            "date_completed, volume, collateral, region_id "
            "FROM ledger_contracts "
            f"WHERE {where} ORDER BY date_completed, contract_id",
            tuple(params),
        ).fetchall()


def list_contract_items_for_owners(owners: list[tuple[str, int]]) -> list[tuple]:
    """(owner_type, owner_id, contract_id, record_id, type_id, quantity,
    is_included, is_singleton, freight_rate_per_m3, freight_m3, freight_frozen)."""
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, contract_id, record_id, type_id, quantity, "
            "is_included, is_singleton, freight_rate_per_m3, freight_m3, freight_frozen "
            "FROM ledger_contract_items "
            f"WHERE {where} ORDER BY contract_id, record_id",
            tuple(params),
        ).fetchall()


def update_contract_item_freight(rows: list[tuple]) -> int:
    """`rows`: (freight_rate_per_m3, freight_m3, freight_frozen, owner_type,
    owner_id, contract_id, record_id)."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "UPDATE ledger_contract_items SET freight_rate_per_m3 = ?, freight_m3 = ?, "
            "freight_frozen = ? WHERE owner_type = ? AND owner_id = ? "
            "AND contract_id = ? AND record_id = ?",
            rows,
        )
    return len(rows)


def update_contract_regions(rows: list[tuple]) -> int:
    """Fill a missing start-location region. `rows`: (region_id, owner_type,
    owner_id, contract_id). Never overwrites a region already stored."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "UPDATE ledger_contracts SET region_id = ? "
            "WHERE owner_type = ? AND owner_id = ? AND contract_id = ? AND region_id IS NULL",
            rows,
        )
    return len(rows)


def blueprint_runs_by_type(owners: list[tuple[str, int]], type_ids: list[int]) -> dict[int, list[int]]:
    """Every archived `runs` value per blueprint type_id. The caller decides
    whether they agree."""
    ids = sorted({int(i) for i in type_ids})
    if not owners or not ids:
        return {}
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        rows = conn.execute(
            f"SELECT type_id, runs FROM ledger_blueprints WHERE {where} AND type_id = ANY(?)",
            (*params, ids),
        ).fetchall()
    out: dict[int, list[int]] = {}
    for type_id, runs in rows:
        out.setdefault(int(type_id), []).append(int(runs))
    return out


def blueprint_runs_by_item(owners: list[tuple[str, int]], item_ids: list[int]) -> dict[int, int]:
    """Last seen `runs` per blueprint item_id from the archive (-1 = BPO)."""
    ids = sorted({int(i) for i in item_ids})
    if not owners or not ids:
        return {}
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        rows = conn.execute(
            f"SELECT item_id, runs FROM ledger_blueprints WHERE {where} AND item_id = ANY(?)",
            (*params, ids),
        ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def invented_blueprint_type_ids() -> frozenset[int]:
    """Blueprint types that are invention outputs (T2/T3 BPCs only exist as
    copies, so a job on one always uses BPC runs)."""
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT product_type_id FROM sde_blueprint_products WHERE activity_id = 8",
        ).fetchall()
    return frozenset(int(r[0]) for r in rows)


def listed_sell_qty_by_type(character_ids: list[int], corporation_ids: list[int]) -> dict[int, float]:
    """Quantity still listed in open sell orders, per type, for the given
    owners (items in a sell order are not in the asset list)."""
    if not character_ids and not corporation_ids:
        return {}
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT type_id, COALESCE(SUM(volume_remain), 0) FROM character_sell_orders "
            "WHERE owner_character_id = ANY(?) OR owner_corporation_id = ANY(?) "
            "GROUP BY type_id",
            ([int(c) for c in character_ids], [int(c) for c in corporation_ids]),
        ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows}


def outstanding_contract_qty_by_type(owners: list[tuple[str, int]]) -> dict[int, float]:
    """Included items of outstanding item-exchange contracts the owners
    issued, per type, each contract counted once even if archived for both a
    character and its corp."""
    if not owners:
        return {}
    where, params = _owner_in_clause(owners)
    where = where.replace("owner_type", "c.owner_type").replace("owner_id", "c.owner_id")
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT type_id, SUM(quantity) FROM ("
            "  SELECT DISTINCT ON (c.contract_id, i.record_id) i.type_id, i.quantity "
            "  FROM ledger_contracts c JOIN ledger_contract_items i "
            "    ON i.owner_type = c.owner_type AND i.owner_id = c.owner_id "
            "   AND i.contract_id = c.contract_id "
            f"  WHERE {where} AND c.status = 'outstanding' AND i.is_included "
            "   AND ((c.owner_type = 'character' AND c.issuer_id = c.owner_id) "
            "     OR (c.owner_type = 'corporation' AND c.issuer_corporation_id = c.owner_id)) "
            ") x GROUP BY type_id",
            tuple(params),
        ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows}


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


def list_cached_valuation_prices(
    type_ids: list[int],
) -> dict[tuple[int, object], tuple[Optional[float], str]]:
    """{(type_id, price_date): (unit_value, source)} for the given types.
    unit_value is None for source "unavailable"."""
    if not type_ids:
        return {}
    unique = list(dict.fromkeys(int(t) for t in type_ids))
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT type_id, price_date, unit_value, source "
            "FROM ledger_valuation_prices WHERE type_id = ANY(?)",
            (unique,),
        ).fetchall()
    return {(int(r[0]), r[1]): (None if r[2] is None else float(r[2]), str(r[3])) for r in rows}


def upsert_valuation_prices(rows: list[tuple]) -> int:
    """`rows`: (type_id, price_date, unit_value, source). Insert-only freeze."""
    if not rows:
        return 0
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO ledger_valuation_prices (type_id, price_date, unit_value, source) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT (tenant_id, type_id, price_date) DO NOTHING",
            rows,
        )
    return len(rows)


def price_sources_on(type_ids: list[int], day) -> dict[int, str]:
    """How each type was valued on `day`: the valuation source (buy_home /
    buy_hub / build), else "forge_average" when only The Forge daily
    average was used, else missing from the dict."""
    if not type_ids:
        return {}
    unique = list(dict.fromkeys(int(t) for t in type_ids))
    with storage.connect() as conn:
        valued = conn.execute(
            "SELECT type_id, source FROM ledger_valuation_prices "
            "WHERE type_id = ANY(?) AND price_date = ? AND source <> 'unavailable'",
            (unique, day),
        ).fetchall()
        forge = conn.execute(
            "SELECT type_id FROM ledger_reference_prices "
            "WHERE type_id = ANY(?) AND price_date = ? AND source <> 'none'",
            (unique, day),
        ).fetchall()
    out = {int(r[0]): "forge_average" for r in forge}
    out.update({int(r[0]): str(r[1]) for r in valued})
    return out


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
    draws: Optional[list[tuple]] = None,
    jobs: Optional[list[tuple]] = None,
    fee_lines: Optional[list[tuple]] = None,
    losses: Optional[list[tuple]] = None,
    capital: Optional[list[tuple]] = None,
    drift: Optional[list[tuple]] = None,
    opening_lines: Optional[list[tuple]] = None,
    frozen_opening: Optional[list[tuple]] = None,
    reset_frozen_opening: bool = False,
    owner_scope_hash: Optional[str] = None,
    costing_version: Optional[int] = None,
) -> int:
    """Fully replace derived costing tables for this tenant (one rebuild).

    The `prod_ledger_runs` row is inserted in the same transaction, after
    every derived row, so a failed write leaves the previous run in place.

    `layers`: remaining layers only. (type_id, unit, quantity_left, unit_cost,
    origin, source_ref, six cost components, layer_date, job_id,
    quantity_original, assumed_me, assumed_decryptor, estimated_origin,
    price_missing, logical_layer_id)
    `results`: one per Production sale. (sale_ref, type_id, quantity, revenue,
    cost, profit, coverage_share, sale_date, gross, sales_tax, broker_fee,
    location_id, price_missing, estimated, warnings list, six cost components)
    `draws`: (consumer_kind, consumer_ref, seq, logical_layer_id, type_id,
    unit, quantity, origin, source_ref, six components, unit_cost,
    estimated_origin, price_missing, assumed_me, assumed_decryptor,
    layer_date, job_id, location_id, unit_price, freight_rate_per_m3)
    `jobs`: see `_JOB_INSERT` below.
    `fee_lines`: (kind, owner_type, owner_id, fee_date, amount, sale_ref)
    `losses`: (loss_kind, job_ref, job_id, loss_date, six components)
    `capital`: (type_id, amount)
    `drift`: (type_id, ledger_qty, esi_qty, drift)
    `opening_lines`: (type_id, raw_quantity, quantity, unit_cost,
    price_source, reference_price)
    `frozen_opening`: (type_id, raw_quantity, cutover_t0, owner_scope_hash)
    for types seen for the first time; existing frozen rows are kept unless
    `reset_frozen_opening` clears them first (T0 or owner scope changed, or
    an explicit reset).
    `costing_version` is stored on the run so the next rebuild can reset
    opening once after a costing change.
    Layer, result and draw tuples may end with `source` (market / contract).
    Returns the new prod_ledger_runs.run_id.
    """
    draws = draws or []
    jobs = jobs or []
    fee_lines = fee_lines or []
    losses = losses or []
    capital = capital or []
    drift = drift or []
    opening_lines = opening_lines or []
    frozen_opening = frozen_opening or []
    result_params = [
        (
            *row[:14],
            Jsonb(list(row[14])),
            *row[15:],
        )
        for row in results
    ]
    with storage.connect() as conn:
        for table in (
            "prod_cost_layers", "prod_realized_results", "prod_cost_draws",
            "prod_job_records", "prod_fee_lines", "prod_job_losses",
            "prod_capital_bpos", "prod_inventory_drift", "prod_opening_lines",
        ):
            conn.execute(f"DELETE FROM {table}")
        if layers:
            conn.executemany(
                "INSERT INTO prod_cost_layers ("
                "type_id, unit, quantity_left, unit_cost, origin, source_ref, "
                "cost_materials_bought, cost_materials_estimated, cost_freight, "
                "cost_job, cost_invention, cost_fees, layer_date, job_id, "
                "quantity_original, assumed_me, assumed_decryptor, estimated_origin, "
                "price_missing, logical_layer_id, source, pool) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                layers,
            )
        if result_params:
            conn.executemany(
                "INSERT INTO prod_realized_results ("
                "sale_ref, type_id, quantity, revenue, cost, profit, "
                "coverage_share, sale_date, gross, sales_tax, broker_fee, "
                "location_id, price_missing, estimated, warnings, "
                "cost_materials_bought, cost_materials_estimated, cost_freight, "
                "cost_job, cost_invention, cost_fees, source, channel, "
                "owner_type, owner_id, fitting_id, issuer_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                result_params,
            )
        if draws:
            conn.executemany(
                "INSERT INTO prod_cost_draws ("
                "consumer_kind, consumer_ref, seq, logical_layer_id, type_id, unit, "
                "quantity, origin, source_ref, "
                "cost_materials_bought, cost_materials_estimated, cost_freight, "
                "cost_job, cost_invention, cost_fees, unit_cost, "
                "estimated_origin, price_missing, assumed_me, assumed_decryptor, "
                "layer_date, job_id, location_id, unit_price, freight_rate_per_m3, source, "
                "channel) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                draws,
            )
        if jobs:
            conn.executemany(
                "INSERT INTO prod_job_records ("
                "job_ref, job_id, owner_type, owner_id, activity_id, "
                "blueprint_type_id, product_type_id, runs, successful_runs, "
                "start_date, end_date, status, state, job_cost, "
                "cost_materials_bought, cost_materials_estimated, cost_freight, "
                "cost_job, cost_invention, cost_fees, "
                "assumed_me, assumed_decryptor, price_missing, no_structure_bonus, "
                "output_layer_id, output_type_id, output_quantity, "
                "output_quantity_left, output_unit, facility_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                jobs,
            )
        if fee_lines:
            conn.executemany(
                "INSERT INTO prod_fee_lines ("
                "kind, owner_type, owner_id, fee_date, amount, sale_ref) "
                "VALUES (?,?,?,?,?,?)",
                fee_lines,
            )
        if losses:
            conn.executemany(
                "INSERT INTO prod_job_losses ("
                "loss_kind, job_ref, job_id, loss_date, "
                "cost_materials_bought, cost_materials_estimated, cost_freight, "
                "cost_job, cost_invention, cost_fees) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                losses,
            )
        if capital:
            conn.executemany(
                "INSERT INTO prod_capital_bpos (type_id, amount) VALUES (?,?)",
                capital,
            )
        if drift:
            conn.executemany(
                "INSERT INTO prod_inventory_drift (type_id, ledger_qty, esi_qty, drift) "
                "VALUES (?,?,?,?)",
                drift,
            )
        if opening_lines:
            conn.executemany(
                "INSERT INTO prod_opening_lines (type_id, raw_quantity, quantity, "
                "unit_cost, price_source, reference_price) VALUES (?,?,?,?,?,?)",
                opening_lines,
            )
        if reset_frozen_opening:
            conn.execute("DELETE FROM prod_opening_frozen")
        if frozen_opening:
            conn.executemany(
                "INSERT INTO prod_opening_frozen (type_id, raw_quantity, cutover_t0, "
                "owner_scope_hash) VALUES (?,?,?,?) ON CONFLICT (tenant_id, type_id) DO NOTHING",
                frozen_opening,
            )
        row = conn.execute(
            "INSERT INTO prod_ledger_runs (cutover_t0, warnings, owner_scope_hash, costing_version) "
            "VALUES (?, ?, ?, ?) RETURNING run_id",
            (cutover_t0, Jsonb(list(warnings)), owner_scope_hash, costing_version),
        ).fetchone()
    return int(row[0])


def latest_costing_version() -> Optional[tuple]:
    """(run_id, costing_version) of the newest rebuild, or None when no run
    exists. costing_version is None for a run written before Phase 5."""
    with storage.connect() as conn:
        return conn.execute(
            "SELECT run_id, costing_version FROM prod_ledger_runs "
            "ORDER BY run_id DESC LIMIT 1",
        ).fetchone()


def latest_ledger_run() -> Optional[tuple]:
    """(run_id, ran_at, cutover_t0, warnings, owner_scope_hash) of the
    newest rebuild, or None."""
    with storage.connect() as conn:
        return conn.execute(
            "SELECT run_id, ran_at, cutover_t0, warnings, owner_scope_hash "
            "FROM prod_ledger_runs ORDER BY run_id DESC LIMIT 1",
        ).fetchone()


def _owner_match_sql(owners: list, prefix: str) -> tuple[str, list]:
    bits = []
    params: list = []
    for owner_type, owner_id in owners:
        bits.append(f"({prefix}owner_type = ? AND {prefix}owner_id = ?)")
        params.extend([str(owner_type), int(owner_id)])
    return "(" + " OR ".join(bits) + ")", params


def _result_scope_sql(
    channel: Optional[str] = None,
    *,
    alias: str = "",
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> tuple[str, list]:
    """AND-clause for one tool's report.

    `channel` None leaves every channel visible. Owner lists, when passed,
    keep a row whose owner is still NULL (written before channels existed)
    and otherwise require the archive owner to be shared with this tool:
    wallet for a market sale, contracts for a contract sale (U3a).
    """
    if not channel:
        return "", []
    prefix = f"{alias}." if alias else ""
    sql = f" AND {prefix}channel = ?"
    params: list = [channel]
    if wallet_owners is None and contract_owners is None:
        return sql, params
    parts = [f"{prefix}owner_id IS NULL"]
    if wallet_owners:
        match, match_params = _owner_match_sql(wallet_owners, prefix)
        parts.append(f"({prefix}source = 'market' AND {match})")
        params.extend(match_params)
    if contract_owners:
        match, match_params = _owner_match_sql(contract_owners, prefix)
        parts.append(f"({prefix}source IS DISTINCT FROM 'market' AND {match})")
        params.extend(match_params)
    return sql + " AND (" + " OR ".join(parts) + ")", params


def count_realized_results(
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> int:
    scope, extra = _result_scope_sql(
        channel, wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM prod_realized_results WHERE TRUE" + scope, extra,
        ).fetchone()
    return int(row[0]) if row else 0


def realized_totals(
    start: datetime,
    end: datetime,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> tuple:
    """One row of period aggregates. Sums ignore NULL sales tax; the tax-null
    count is separate so a missing tax is not reported as zero tax. A
    contract sale stores NULL tax on purpose and is not a missing tax."""
    scope, extra = _result_scope_sql(
        channel, wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        return conn.execute(
            "SELECT COALESCE(SUM(revenue), 0), COALESCE(SUM(cost), 0), "
            "COALESCE(SUM(profit), 0), COALESCE(SUM(quantity), 0), "
            "COALESCE(SUM(cost_materials_bought), 0), "
            "COALESCE(SUM(cost_materials_estimated), 0), "
            "COALESCE(SUM(cost_freight), 0), COALESCE(SUM(cost_job), 0), "
            "COALESCE(SUM(cost_invention), 0), COALESCE(SUM(cost_fees), 0), "
            "COALESCE(SUM(sales_tax), 0), "
            "COUNT(*) FILTER (WHERE sales_tax IS NULL AND source IS DISTINCT FROM 'contract'), "
            "COALESCE(BOOL_OR(price_missing), FALSE), COUNT(*) "
            "FROM prod_realized_results WHERE sale_date >= ? AND sale_date < ?"
            + scope,
            (start, end, *extra),
        ).fetchone()


def realized_by_day(
    start: datetime,
    end: datetime,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> list[tuple]:
    scope, extra = _result_scope_sql(
        channel, wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        return conn.execute(
            "SELECT (sale_date AT TIME ZONE 'UTC')::date, "
            "COALESCE(SUM(revenue), 0), COALESCE(SUM(cost), 0), COALESCE(SUM(profit), 0) "
            "FROM prod_realized_results WHERE sale_date >= ? AND sale_date < ?"
            + scope + " GROUP BY 1 ORDER BY 1",
            (start, end, *extra),
        ).fetchall()


def realized_by_type(
    start: datetime,
    end: datetime,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> list[tuple]:
    scope, extra = _result_scope_sql(
        channel, wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        return conn.execute(
            "SELECT type_id, COALESCE(SUM(quantity), 0), COALESCE(SUM(revenue), 0), "
            "COALESCE(SUM(cost), 0), COALESCE(SUM(profit), 0), "
            "COALESCE(SUM(cost_materials_bought), 0), "
            "COALESCE(SUM(cost_materials_estimated), 0), "
            "COALESCE(SUM(cost_freight), 0), COALESCE(SUM(cost_job), 0), "
            "COALESCE(SUM(cost_invention), 0), COALESCE(SUM(cost_fees), 0), "
            "COALESCE(BOOL_OR(price_missing), FALSE) "
            "FROM prod_realized_results WHERE sale_date >= ? AND sale_date < ?"
            + scope + " GROUP BY type_id ORDER BY type_id",
            (start, end, *extra),
        ).fetchall()


def realized_draw_flags_by_type(
    start: datetime,
    end: datetime,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> list[tuple]:
    """(type_id, assumed_me, assumed_decryptor) for sales in the period."""
    scope, extra = _result_scope_sql(
        channel, alias="r", wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        return conn.execute(
            "SELECT r.type_id, COALESCE(BOOL_OR(d.assumed_me), FALSE), "
            "COALESCE(BOOL_OR(d.assumed_decryptor), FALSE) "
            "FROM prod_realized_results r "
            "JOIN prod_cost_draws d ON d.consumer_kind = 'sale' AND d.consumer_ref = r.sale_ref "
            "WHERE r.sale_date >= ? AND r.sale_date < ?"
            + scope + " GROUP BY r.type_id",
            (start, end, *extra),
        ).fetchall()


def realized_warnings_by_type(
    start: datetime,
    end: datetime,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> list[tuple]:
    scope, extra = _result_scope_sql(
        channel, alias="r", wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        return conn.execute(
            "SELECT r.type_id, w "
            "FROM prod_realized_results r, "
            "LATERAL jsonb_array_elements_text(COALESCE(r.warnings, '[]'::jsonb)) AS w "
            "WHERE r.sale_date >= ? AND r.sale_date < ?" + scope,
            (start, end, *extra),
        ).fetchall()


def list_sales_for_type(
    type_id: int,
    start: datetime,
    end: datetime,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> list[tuple]:
    scope, extra = _result_scope_sql(
        channel, wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        return conn.execute(
            "SELECT sale_ref, type_id, quantity, revenue, cost, profit, coverage_share, "
            "sale_date, gross, sales_tax, broker_fee, location_id, price_missing, "
            "estimated, warnings, cost_materials_bought, cost_materials_estimated, "
            "cost_freight, cost_job, cost_invention, cost_fees, source "
            "FROM prod_realized_results "
            "WHERE type_id = ? AND sale_date >= ? AND sale_date < ?"
            + scope + " ORDER BY sale_date, sale_ref",
            (int(type_id), start, end, *extra),
        ).fetchall()


def list_scoped_results(
    start: datetime,
    end: datetime,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> list[tuple]:
    """Sales in the window, including the doctrine fitting and the issuer.

    Column order matches `get_realized_sale`, then owner_type, owner_id,
    fitting_id, issuer_id.
    """
    scope, extra = _result_scope_sql(
        channel, wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        return conn.execute(
            "SELECT sale_ref, type_id, quantity, revenue, cost, profit, coverage_share, "
            "sale_date, gross, sales_tax, broker_fee, location_id, price_missing, "
            "estimated, warnings, cost_materials_bought, cost_materials_estimated, "
            "cost_freight, cost_job, cost_invention, cost_fees, source, "
            "owner_type, owner_id, fitting_id, issuer_id "
            "FROM prod_realized_results "
            "WHERE sale_date >= ? AND sale_date < ?"
            + scope + " ORDER BY sale_date, sale_ref",
            (start, end, *extra),
        ).fetchall()


def produced_draw_totals(
    start: datetime,
    end: datetime,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> tuple:
    """(contracts, units, cost) of produced-origin draws on sales in the window.

    Cost is the draw's own components (materials, freight, job, invention,
    fees on the draw). The sale's broker fee is not in that cost.
    """
    scope, extra = _result_scope_sql(
        channel, alias="r", wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT r.sale_ref) FILTER (WHERE d.origin = 'produced'), "
            "COALESCE(SUM(d.quantity) FILTER (WHERE d.origin = 'produced'), 0), "
            "COALESCE(SUM(d.cost_materials_bought + d.cost_materials_estimated "
            "+ d.cost_freight + d.cost_job + d.cost_invention + d.cost_fees) "
            "FILTER (WHERE d.origin = 'produced'), 0) "
            "FROM prod_realized_results r "
            "JOIN prod_cost_draws d ON d.consumer_kind = 'sale' AND d.consumer_ref = r.sale_ref "
            "WHERE r.sale_date >= ? AND r.sale_date < ?" + scope,
            (start, end, *extra),
        ).fetchone()
    return row


def get_realized_sale(
    sale_ref: str,
    *,
    channel: Optional[str] = None,
    wallet_owners: Optional[list] = None,
    contract_owners: Optional[list] = None,
) -> Optional[tuple]:
    scope, extra = _result_scope_sql(
        channel, wallet_owners=wallet_owners, contract_owners=contract_owners,
    )
    with storage.connect() as conn:
        return conn.execute(
            "SELECT sale_ref, type_id, quantity, revenue, cost, profit, coverage_share, "
            "sale_date, gross, sales_tax, broker_fee, location_id, price_missing, "
            "estimated, warnings, cost_materials_bought, cost_materials_estimated, "
            "cost_freight, cost_job, cost_invention, cost_fees, source "
            "FROM prod_realized_results WHERE sale_ref = ?" + scope,
            (sale_ref, *extra),
        ).fetchone()


def list_draws(consumer_kind: str, refs: list[str]) -> list[tuple]:
    if not refs:
        return []
    with storage.connect() as conn:
        return conn.execute(
            "SELECT consumer_ref, seq, logical_layer_id, type_id, unit, quantity, origin, "
            "source_ref, cost_materials_bought, cost_materials_estimated, cost_freight, "
            "cost_job, cost_invention, cost_fees, unit_cost, estimated_origin, "
            "price_missing, assumed_me, assumed_decryptor, layer_date, job_id, "
            "location_id, unit_price, freight_rate_per_m3, source "
            "FROM prod_cost_draws WHERE consumer_kind = ? AND consumer_ref = ANY(?) "
            "ORDER BY consumer_ref, seq",
            (consumer_kind, list(refs)),
        ).fetchall()


def list_jobs_by_output_layers(layer_ids: list[int]) -> list[tuple]:
    ids = [int(i) for i in layer_ids if i is not None]
    if not ids:
        return []
    with storage.connect() as conn:
        return conn.execute(
            "SELECT job_ref, job_id, activity_id, blueprint_type_id, product_type_id, "
            "runs, successful_runs, start_date, end_date, status, state, job_cost, "
            "cost_materials_bought, cost_materials_estimated, cost_freight, cost_job, "
            "cost_invention, cost_fees, assumed_me, assumed_decryptor, price_missing, "
            "no_structure_bonus, output_layer_id, output_type_id, output_quantity, "
            "output_quantity_left, output_unit, facility_id "
            "FROM prod_job_records WHERE output_layer_id = ANY(?)",
            (ids,),
        ).fetchall()


def list_job_records() -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT job_ref, job_id, owner_type, owner_id, activity_id, "
            "blueprint_type_id, product_type_id, runs, successful_runs, "
            "start_date, end_date, status, state, job_cost, "
            "cost_materials_bought, cost_materials_estimated, cost_freight, "
            "cost_job, cost_invention, cost_fees, assumed_me, assumed_decryptor, "
            "price_missing, no_structure_bonus, output_layer_id, output_type_id, "
            "output_quantity, output_quantity_left, output_unit, facility_id "
            "FROM prod_job_records ORDER BY start_date, job_id",
        ).fetchall()


def list_remaining_layers() -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT type_id, unit, quantity_left, unit_cost, origin, "
            "assumed_me, assumed_decryptor, estimated_origin, price_missing, pool "
            "FROM prod_cost_layers WHERE quantity_left > 0 "
            "ORDER BY type_id, logical_layer_id",
        ).fetchall()


def list_inventory_drift() -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT type_id, ledger_qty, esi_qty, drift FROM prod_inventory_drift "
            "ORDER BY type_id",
        ).fetchall()


def fee_totals_between(start_day, end_day) -> tuple:
    """(fees_without_sale, fees_on_other_trading) for UTC days that overlap
    [start_day, end_day). end_day is exclusive."""
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT "
            "COALESCE(SUM(amount) FILTER (WHERE kind = 'without_sale'), 0), "
            "COALESCE(SUM(amount) FILTER (WHERE kind = 'other_trading'), 0) "
            "FROM prod_fee_lines WHERE fee_date >= ? AND fee_date < ?",
            (start_day, end_day),
        ).fetchone()
    return (float(row[0]), float(row[1])) if row else (0.0, 0.0)


def list_fee_lines(kind: str) -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, fee_date, amount, sale_ref "
            "FROM prod_fee_lines WHERE kind = ? ORDER BY fee_date, owner_id",
            (kind,),
        ).fetchall()


def loss_totals_between(start: datetime, end: datetime) -> tuple:
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT "
            "COALESCE(SUM(cost_materials_bought + cost_materials_estimated + cost_freight "
            "+ cost_job + cost_invention + cost_fees) "
            "FILTER (WHERE loss_kind = 'cancelled'), 0), "
            "COALESCE(SUM(cost_materials_bought + cost_materials_estimated + cost_freight "
            "+ cost_job + cost_invention + cost_fees) "
            "FILTER (WHERE loss_kind = 'failed_invention'), 0) "
            "FROM prod_job_losses WHERE loss_date >= ? AND loss_date < ?",
            (start, end),
        ).fetchone()
    return (float(row[0]), float(row[1])) if row else (0.0, 0.0)


def capital_in_bpos_total() -> float:
    with storage.connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM prod_capital_bpos").fetchone()
    return float(row[0]) if row else 0.0


def inventory_at_cost_total() -> float:
    """Unsold produced and opening item layers (plan section 9a). Bought
    leftovers stay on the inventory list and are not in this balance."""
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity_left * unit_cost), 0) FROM prod_cost_layers "
            "WHERE unit = 'item' AND origin IN ('produced', 'opening')",
        ).fetchone()
    return float(row[0]) if row else 0.0


def manual_entry_totals(start: datetime, end: datetime) -> tuple:
    """(cost, revenue) of manual ledger entries in the period. `amount` is
    the entry's total ISK; quantity is not multiplied in."""
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT "
            "COALESCE(SUM(amount) FILTER (WHERE entry_kind = 'cost'), 0), "
            "COALESCE(SUM(amount) FILTER (WHERE entry_kind = 'revenue'), 0) "
            "FROM ledger_manual_entries "
            "WHERE entry_kind IN ('cost', 'revenue') AND entry_date >= ? AND entry_date < ?",
            (start, end),
        ).fetchone()
    return (float(row[0]), float(row[1])) if row else (0.0, 0.0)


def list_ledger_sync_state() -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, data_kind, last_synced_at FROM ledger_sync_state",
        ).fetchall()


def earliest_archived_transaction(owners: list[tuple[str, int]]) -> Optional[datetime]:
    if not owners:
        return None
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        row = conn.execute(
            f"SELECT MIN(first_seen_at) FROM ledger_wallet_transactions WHERE {where}",
            tuple(params),
        ).fetchone()
    return row[0] if row else None


def list_all_archive_gaps() -> list[tuple]:
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, data_kind, gap_start, gap_end "
            "FROM ledger_archive_gaps ORDER BY gap_start",
        ).fetchall()


def unresolved_regions(owners: list[tuple[str, int]]) -> list[tuple]:
    """(location_id, count) of archived transactions whose region is still NULL."""
    if not owners:
        return []
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT location_id, COUNT(*) FROM ledger_wallet_transactions "
            f"WHERE region_id IS NULL AND {where} GROUP BY location_id ORDER BY location_id",
            tuple(params),
        ).fetchall()


def structure_bonus_applied_by_job(owners: list[tuple[str, int]]) -> dict[tuple, Optional[bool]]:
    """(owner_type, owner_id, job_id) -> True/False, or None when every frozen
    input left the flag unknown. One query."""
    if not owners:
        return {}
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT owner_type, owner_id, job_id, bool_and(structure_bonus_applied) "
            f"FROM ledger_job_inputs WHERE {where} "
            "GROUP BY owner_type, owner_id, job_id",
            tuple(params),
        ).fetchall()
    return {
        (r[0], int(r[1]), int(r[2])): (None if r[3] is None else bool(r[3]))
        for r in rows
    }


def insert_manual_entry(
    *,
    entry_kind: str,
    amount: float,
    entry_date: datetime,
    type_id: Optional[int],
    quantity: Optional[float],
    note: Optional[str],
) -> int:
    with storage.connect() as conn:
        row = conn.execute(
            "INSERT INTO ledger_manual_entries "
            "(entry_kind, type_id, quantity, amount, entry_date, note) "
            "VALUES (?,?,?,?,?,?) RETURNING entry_id",
            (entry_kind, type_id, quantity, float(amount), entry_date, note),
        ).fetchone()
    return int(row[0])


def replace_opening_cost(type_id: int, unit_cost: float, entry_date: datetime) -> int:
    """One opening unit-cost override per type. The previous row is removed
    in the same transaction as the insert."""
    with storage.connect() as conn:
        conn.execute(
            "DELETE FROM ledger_manual_entries "
            "WHERE entry_kind = 'opening_cost' AND type_id = ?",
            (int(type_id),),
        )
        row = conn.execute(
            "INSERT INTO ledger_manual_entries "
            "(entry_kind, type_id, quantity, amount, entry_date, note) "
            "VALUES ('opening_cost', ?, NULL, ?, ?, ?) RETURNING entry_id",
            (int(type_id), float(unit_cost), entry_date, "opening unit cost override"),
        ).fetchone()
    return int(row[0])


def count_freight_rate_changes(
    rates: dict,
    *,
    clear_location_ids: Optional[list] = None,
    clear_region_ids: Optional[list] = None,
) -> int:
    """Purchases `reapply_freight_rates` would change: a hub rate that no
    longer matches, or freight that U4/ST3 now says should be absent."""
    return _freight_reapply(rates, clear_location_ids or [], clear_region_ids or [], apply=False)


def reapply_freight_rates(
    rates: dict,
    *,
    clear_location_ids: Optional[list] = None,
    clear_region_ids: Optional[list] = None,
) -> int:
    """Write the current per-hub rate onto purchases that already have a
    freight volume. Home-structure, home-region and station-trade buys lose
    a rate that an earlier ingest froze. Rows that never had freight stay
    without it."""
    return _freight_reapply(rates, clear_location_ids or [], clear_region_ids or [], apply=True)


def _freight_reapply(rates: dict, clear_locations: list, clear_regions: list, *, apply: bool) -> int:
    location_ids = [int(v) for v in clear_locations if v is not None]
    region_ids = [int(v) for v in clear_regions if v is not None]
    changed = 0
    with storage.connect() as conn:
        if location_ids or region_ids:
            clauses = []
            params: list = []
            if location_ids:
                clauses.append("location_id = ANY(?)")
                params.append(location_ids)
            if region_ids:
                clauses.append("region_id = ANY(?)")
                params.append(region_ids)
            where = "freight_m3 IS NOT NULL AND (" + " OR ".join(clauses) + ")"
            if apply:
                cur = conn.execute(
                    "UPDATE ledger_wallet_transactions SET freight_rate_per_m3 = NULL, "
                    "freight_m3 = NULL WHERE " + where,
                    params,
                )
                changed += int(cur.rowcount or 0)
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM ledger_wallet_transactions WHERE " + where, params,
                ).fetchone()
                changed += int(row[0]) if row else 0
        for region_id, rate in rates.items():
            if int(region_id) in region_ids:
                continue
            if apply:
                cur = conn.execute(
                    "UPDATE ledger_wallet_transactions SET freight_rate_per_m3 = ? "
                    "WHERE freight_m3 IS NOT NULL AND region_id = ? "
                    "AND freight_rate_per_m3 IS DISTINCT FROM ?",
                    (float(rate), int(region_id), float(rate)),
                )
                changed += int(cur.rowcount or 0)
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM ledger_wallet_transactions "
                    "WHERE freight_m3 IS NOT NULL AND region_id = ? "
                    "AND freight_rate_per_m3 IS DISTINCT FROM ?",
                    (int(region_id), float(rate)),
                ).fetchone()
                changed += int(row[0]) if row else 0
    return changed


def list_frozen_opening() -> list[tuple]:
    """(type_id, raw_quantity, cutover_t0, owner_scope_hash, frozen_at)."""
    with storage.connect() as conn:
        return conn.execute(
            "SELECT type_id, raw_quantity, cutover_t0, owner_scope_hash, frozen_at "
            "FROM prod_opening_frozen ORDER BY type_id",
        ).fetchall()


def list_opening_lines() -> list[tuple]:
    """Opening inventory of the latest rebuild joined with the frozen date.
    (type_id, raw_quantity, quantity, unit_cost, price_source,
    reference_price, frozen_at)."""
    with storage.connect() as conn:
        return conn.execute(
            "SELECT o.type_id, o.raw_quantity, o.quantity, o.unit_cost, o.price_source, "
            "o.reference_price, f.frozen_at "
            "FROM prod_opening_lines o "
            "LEFT JOIN prod_opening_frozen f ON f.type_id = o.type_id "
            "ORDER BY o.type_id",
        ).fetchall()


def list_manual_entries() -> list[tuple]:
    """(entry_id, entry_kind, type_id, quantity, amount, entry_date, note,
    created_at), newest entry date first."""
    with storage.connect() as conn:
        return conn.execute(
            "SELECT entry_id, entry_kind, type_id, quantity, amount, entry_date, note, created_at "
            "FROM ledger_manual_entries ORDER BY entry_date DESC, entry_id DESC",
        ).fetchall()


def delete_manual_entry(entry_id: int) -> bool:
    with storage.connect() as conn:
        cur = conn.execute(
            "DELETE FROM ledger_manual_entries WHERE entry_id = ?", (int(entry_id),),
        )
    return bool(cur.rowcount)


def archive_gap_counts() -> dict[tuple, int]:
    """(owner_type, owner_id, data_kind) -> number of recorded gaps."""
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT owner_type, owner_id, data_kind, COUNT(*) FROM ledger_archive_gaps "
            "GROUP BY owner_type, owner_id, data_kind",
        ).fetchall()
    return {(r[0], int(r[1]), str(r[2])): int(r[3]) for r in rows}


def planned_unit_cost_by_type(start: datetime, end: datetime) -> dict[int, tuple]:
    """type_id -> (covered_qty, covered_planned_cost, covered_realized_cost)
    for produced-layer sale draws whose job has a non-null planned unit cost.
    covered_planned_cost is sum(draw qty x planned unit cost);
    covered_realized_cost is the realized cost of those same draws."""
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT r.type_id, "
            "COALESCE(SUM(d.quantity) FILTER (WHERE p.planned_unit_cost IS NOT NULL), 0), "
            "COALESCE(SUM(d.quantity * p.planned_unit_cost) "
            "FILTER (WHERE p.planned_unit_cost IS NOT NULL), 0), "
            "COALESCE(SUM(d.cost_materials_bought + d.cost_materials_estimated + d.cost_freight "
            "+ d.cost_job + d.cost_invention + d.cost_fees) "
            "FILTER (WHERE p.planned_unit_cost IS NOT NULL), 0) "
            "FROM prod_realized_results r "
            "JOIN prod_cost_draws d ON d.consumer_kind = 'sale' AND d.consumer_ref = r.sale_ref "
            "AND d.origin = 'produced' "
            "JOIN prod_job_records j ON j.output_layer_id = d.logical_layer_id "
            "LEFT JOIN ledger_job_planned_costs p "
            "ON p.owner_type = j.owner_type AND p.owner_id = j.owner_id AND p.job_id = j.job_id "
            "WHERE r.sale_date >= ? AND r.sale_date < ? "
            "GROUP BY r.type_id",
            (start, end),
        ).fetchall()
    return {int(r[0]): (float(r[1]), float(r[2]), float(r[3])) for r in rows}


def job_refs_with_contract_draws() -> set[str]:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT consumer_ref FROM prod_cost_draws "
            "WHERE consumer_kind = 'job' AND source = 'contract'",
        ).fetchall()
    return {str(r[0]) for r in rows}


def list_forge_home_couriers(
    owners: list[tuple[str, int]], home_location_id: int,
) -> list[tuple]:
    """Finished courier contracts this tenant issued, from The Forge to the
    home structure, deduped by contract_id.
    (contract_id, date_completed, volume, reward)."""
    if not owners:
        return []
    where, params = _owner_in_clause(owners)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT DISTINCT ON (contract_id) contract_id, date_completed, volume, reward "
            "FROM ledger_contracts "
            f"WHERE {where} AND type = 'courier' AND status = 'finished' "
            "AND date_completed IS NOT NULL AND region_id = ? AND end_location_id = ? "
            "ORDER BY contract_id, date_completed",
            (*params, THE_FORGE_REGION_ID, int(home_location_id)),
        ).fetchall()


_HIDDEN_ARCHIVE_KINDS = {
    "wallet": (
        ("ledger_wallet_transactions", "ledger_wallet_journal"),
        ("wallet_transactions", "wallet_journal"),
    ),
    "industry_jobs": (
        ("ledger_industry_jobs", "ledger_job_inputs", "ledger_job_planned_costs"),
        ("industry_jobs",),
    ),
    "market_orders": (
        ("ledger_market_orders",),
        ("market_orders",),
    ),
    "contracts": (
        ("ledger_contracts", "ledger_contract_items"),
        ("contracts",),
    ),
}


def hidden_archive_kind(kind: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        return _HIDDEN_ARCHIVE_KINDS[kind]
    except KeyError as exc:
        raise ValueError(f"unknown archive kind {kind}") from exc


def count_owner_archive_rows(owner_type: str, owner_id: int, kind: str) -> int:
    tables, _sync = hidden_archive_kind(kind)
    total = 0
    with storage.connect() as conn:
        for table in tables:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE owner_type = ? AND owner_id = ?",
                (owner_type, int(owner_id)),
            ).fetchone()
            total += int(row[0]) if row else 0
    return total


def delete_owner_archive_kind(owner_type: str, owner_id: int, kind: str) -> int:
    """Delete one owner's archived rows of `kind`, plus that kind's sync
    state and gap rows. Returns the archive-row count that was deleted."""
    tables, sync_kinds = hidden_archive_kind(kind)
    with storage.connect() as conn:
        deleted = 0
        for table in tables:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE owner_type = ? AND owner_id = ?",
                (owner_type, int(owner_id)),
            )
            deleted += int(cur.rowcount or 0)
        conn.execute(
            "DELETE FROM ledger_sync_state WHERE owner_type = ? AND owner_id = ? "
            "AND data_kind = ANY(?)",
            (owner_type, int(owner_id), list(sync_kinds)),
        )
        conn.execute(
            "DELETE FROM ledger_archive_gaps WHERE owner_type = ? AND owner_id = ? "
            "AND data_kind = ANY(?)",
            (owner_type, int(owner_id), list(sync_kinds)),
        )
    return deleted


_CORP_SUFFIX = " (corp)"


def stored_owner_names(ids: list[int]) -> dict[int, str]:
    """Character / corporation names the app already stored from earlier ESI
    syncs, no ESI call: corporations from corp_assets (owner_name is
    "<name> (corp)"), characters from the installer names of synced industry
    jobs (corpmates included)."""
    unique = sorted({int(i) for i in ids if i})
    if not unique:
        return {}
    out: dict[int, str] = {}
    with storage.connect() as conn:
        for owner_id, name in conn.execute(
            "SELECT DISTINCT owner_corporation_id, owner_name FROM corp_assets "
            "WHERE owner_corporation_id = ANY(?)", (unique,),
        ).fetchall():
            if name:
                text = str(name)
                out[int(owner_id)] = text[: -len(_CORP_SUFFIX)] if text.endswith(_CORP_SUFFIX) else text
        for table in ("character_industry_jobs", "corp_industry_jobs"):
            for installer_id, name in conn.execute(
                f"SELECT DISTINCT installer_id, installer_name FROM {table} "
                "WHERE installer_id = ANY(?) AND installer_name IS NOT NULL", (unique,),
            ).fetchall():
                out.setdefault(int(installer_id), str(name))
    return out


def planned_unit_costs_by_job() -> dict[tuple[str, int, int], float]:
    """(owner_type, owner_id, job_id) -> planned unit cost, where one was frozen."""
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT owner_type, owner_id, job_id, planned_unit_cost FROM ledger_job_planned_costs "
            "WHERE planned_unit_cost IS NOT NULL",
        ).fetchall()
    return {(str(r[0]), int(r[1]), int(r[2])): float(r[3]) for r in rows}


_JOB_TOTAL = ("cost_materials_bought + cost_materials_estimated + cost_freight + cost_job "
              "+ cost_invention + cost_fees")


def job_spend_between(start: datetime, end: datetime) -> list[tuple]:
    """Per activity, costed jobs started in [start, end):
    (activity_id, jobs, fees paid, total cost incl. consumed inputs)."""
    with storage.connect() as conn:
        return conn.execute(
            f"SELECT activity_id, count(*), COALESCE(sum(job_cost), 0), COALESCE(sum({_JOB_TOTAL}), 0) "
            "FROM prod_job_records WHERE start_date >= ? AND start_date < ? GROUP BY activity_id",
            (start, end),
        ).fetchall()


def running_job_cost() -> tuple:
    """(running jobs, cost already put into them) as of the latest rebuild."""
    with storage.connect() as conn:
        return conn.execute(
            f"SELECT count(*), COALESCE(sum({_JOB_TOTAL}), 0) FROM prod_job_records WHERE state = 'wip'",
        ).fetchone()


def produced_stock_value() -> float:
    """Produced units still in stock, at their cost (latest rebuild)."""
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(sum(quantity_left * (cost_materials_bought + cost_materials_estimated "
            "+ cost_freight + cost_job + cost_invention + cost_fees)), 0) "
            "FROM prod_cost_layers WHERE origin = 'produced'",
        ).fetchone()
    return float(row[0] or 0.0)

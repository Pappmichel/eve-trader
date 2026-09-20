"""Conservative ESI-access migration (docs/ESI_ACCESS_PLAN.md decision 13).

Walks `tenant_tokens` for the current tenant, inserts sharing rows from
each prefix's historical grant, ticks group-3 capabilities implied by
that prefix, and leaves tokens in place. Idempotent (`ON CONFLICT DO
NOTHING`). No tool-package imports — prefix → (tool, kinds, capabilities)
is data here, the same strings as the registry.

Corp sharing needs a corporation_id. Tokens do not store one; the
default path resolves it via `ESIClient.character_public_info` (public,
no auth — same shape as `trade_reconciliation._corps_for_characters`).
`corporation_ids` is an override: a supplied character is not looked
up. A failed lookup falls back to character-only sharing for that
character (logged), it is not the normal path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .. import storage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PrefixGrant:
    tool_key: str
    character_kinds: tuple[str, ...]
    corp_kinds: tuple[str, ...]
    capabilities: tuple[str, ...]


# Settled decision 13. Corp kinds only for prefixes the plan names as
# carrying a corp variant (producer, doctrine, doctrine-assets) — not
# auto-widened to Sorting or Trading, and not inferred from later scope
# additions such as Phase 8 corp wallets on buyer/seller.
_PREFIX_GRANT: dict[str, _PrefixGrant] = {
    "buyer": _PrefixGrant(
        "trading",
        ("market_orders", "wallet", "assets"),
        (),
        ("structure_market_book",),
    ),
    "seller": _PrefixGrant(
        "trading",
        ("market_orders", "wallet", "assets"),
        (),
        ("structure_market_book",),
    ),
    "producer": _PrefixGrant(
        "production",
        ("assets", "industry_jobs", "blueprints", "market_orders", "skills"),
        ("assets", "industry_jobs", "blueprints", "market_orders"),
        ("structure_name_resolution", "structure_market_book"),
    ),
    "doctrine": _PrefixGrant(
        "doctrine",
        ("contracts",),
        ("contracts",),
        ("structure_name_resolution",),
    ),
    "doctrine-assets": _PrefixGrant(
        "doctrine",
        ("assets",),
        ("assets",),
        (),
    ),
    "trader": _PrefixGrant(
        "station_trading",
        ("market_orders", "skills"),
        (),
        (),
    ),
}


def _role_prefix(role: str) -> Optional[str]:
    if role == "gate":
        return None
    if ":" in role:
        return role.split(":", 1)[0]
    return role


def _corporation_id_for(
    character_id: int,
    *,
    needs_corp: bool,
    overrides: dict[int, int],
    cache: dict[int, Optional[int]],
    client,
    missing_corp: list[int],
):
    """Override wins and skips ESI. Otherwise public-info, cached per
    character. `client` is an ESIClient or None; created by the caller
    on first use so a network-free override run never constructs one.
    """
    if character_id in overrides:
        return overrides[character_id]
    if not needs_corp:
        return None
    if character_id in cache:
        return cache[character_id]
    try:
        info = client.character_public_info(character_id)
    except Exception:  # noqa: BLE001 - character-only fallback; do not abort the tenant
        log.warning(
            "Conservative sharing: character %s: public-info fetch failed; "
            "character rows written, corp rows skipped",
            character_id, exc_info=True,
        )
        cache[character_id] = None
        missing_corp.append(character_id)
        return None
    corp_id = info.get("corporation_id") if isinstance(info, dict) else None
    if not corp_id:
        log.warning(
            "Conservative sharing: character %s: public-info returned no "
            "corporation_id; character rows written, corp rows skipped",
            character_id,
        )
        cache[character_id] = None
        missing_corp.append(character_id)
        return None
    cache[character_id] = int(corp_id)
    return cache[character_id]


def backfill_conservative_sharing(
    corporation_ids: Optional[dict[int, int]] = None,
) -> dict:
    """Insert sharing + capability rows for the current tenant from
    `tenant_tokens`. Resolves each character's corporation_id via
    `character_public_info` when a prefix has corp kinds.
    `corporation_ids` is an override (tests / network-free runs): a
    supplied character is not looked up.

    Also fills snapshot owner-id columns where a name/id match exists, and
    `sorting_intake_sources` owner ids. Unmatched intake rows keep
    `owner_name` and are logged, not deleted.

    Returns counts for tests / the CLI. Safe to re-run.
    """
    from ..esi_client import ESIClient  # local import: avoids a hard dependency for callers that don't need it

    overrides = dict(corporation_ids or {})
    tokens = storage.load_all_tenant_tokens()
    sharing_attempted = 0
    capabilities_attempted = 0
    skipped_prefixes: list[str] = []
    missing_corp: list[int] = []
    resolved_cache: dict[int, Optional[int]] = {}
    client = None

    with storage.connect() as conn:
        for role, record in tokens.items():
            prefix = _role_prefix(role)
            grant = _PREFIX_GRANT.get(prefix or "")
            if grant is None:
                skipped_prefixes.append(role)
                continue
            character_id = int(record["character_id"])
            character_name = record.get("character_name") or ""
            for kind in grant.character_kinds:
                conn.execute(
                    "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                    "VALUES ('character', ?, ?, ?) ON CONFLICT DO NOTHING",
                    (character_id, kind, grant.tool_key),
                )
                sharing_attempted += 1
            for cap in grant.capabilities:
                conn.execute(
                    "INSERT INTO esi_character_capabilities (character_id, capability_key) "
                    "VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (character_id, cap),
                )
                capabilities_attempted += 1
            if grant.corp_kinds and character_id not in overrides and client is None:
                client = ESIClient()
            corp_id = _corporation_id_for(
                character_id,
                needs_corp=bool(grant.corp_kinds),
                overrides=overrides,
                cache=resolved_cache,
                client=client,
                missing_corp=missing_corp,
            )
            if grant.corp_kinds and corp_id is None:
                # Warning already emitted by the lookup (or override omitted
                # a needed id). Keep the list unique for the CLI.
                if character_id not in missing_corp:
                    missing_corp.append(character_id)
                    log.warning(
                        "Conservative sharing: character %s prefix %s has corp kinds "
                        "but no corporation_id; character rows written, corp rows skipped",
                        character_id, prefix,
                    )
            elif corp_id is not None:
                for kind in grant.corp_kinds:
                    conn.execute(
                        "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
                        "VALUES ('corporation', ?, ?, ?) ON CONFLICT DO NOTHING",
                        (corp_id, kind, grant.tool_key),
                    )
                    sharing_attempted += 1
            _fill_character_snapshot_ids(conn, character_id, character_name)

        unmatched_intake = _backfill_sorting_intake(conn, tokens)

    return {
        "tokens": len(tokens),
        "sharing_inserts_attempted": sharing_attempted,
        "capability_inserts_attempted": capabilities_attempted,
        "skipped_roles": skipped_prefixes,
        "characters_missing_corporation_id": sorted(set(missing_corp)),
        "unmatched_sorting_intake_ids": unmatched_intake,
    }


def _fill_character_snapshot_ids(conn, character_id: int, character_name: str) -> None:
    if character_name:
        conn.execute(
            "UPDATE character_assets SET owner_character_id = ? "
            "WHERE owner_name = ? AND owner_character_id IS NULL",
            (character_id, character_name),
        )
        conn.execute(
            "UPDATE character_slots SET owner_character_id = ? "
            "WHERE character_name = ? AND owner_character_id IS NULL",
            (character_id, character_name),
        )
        conn.execute(
            "UPDATE character_sell_orders SET owner_character_id = ? "
            "WHERE character_name = ? AND owner_character_id IS NULL",
            (character_id, character_name),
        )
        conn.execute(
            "UPDATE character_industry_jobs SET owner_character_id = ? "
            "WHERE installer_name = ? AND owner_character_id IS NULL",
            (character_id, character_name),
        )
    conn.execute(
        "UPDATE character_industry_jobs SET owner_character_id = installer_id "
        "WHERE installer_id = ? AND owner_character_id IS NULL",
        (character_id,),
    )
    if character_name and _table_exists(conn, "doctrine_character_assets"):
        conn.execute(
            "UPDATE doctrine_character_assets SET owner_character_id = ? "
            "WHERE owner_name = ? AND owner_character_id IS NULL",
            (character_id, character_name),
        )


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _backfill_sorting_intake(conn, tokens: dict[str, dict]) -> list[int]:
    name_to_character: dict[str, int] = {}
    for record in tokens.values():
        name = record.get("character_name")
        if name:
            name_to_character[name] = int(record["character_id"])
    rows = conn.execute(
        "SELECT id, source_kind, owner_name, owner_character_id, owner_corporation_id "
        "FROM sorting_intake_sources",
    ).fetchall()
    unmatched: list[int] = []
    for source_id, source_kind, owner_name, existing_char, existing_corp in rows:
        if source_kind == "character":
            if existing_char is not None:
                continue
            cid = name_to_character.get(owner_name or "")
            if cid is None:
                row = conn.execute(
                    "SELECT owner_character_id FROM character_assets "
                    "WHERE owner_name = ? AND owner_character_id IS NOT NULL LIMIT 1",
                    (owner_name,),
                ).fetchone()
                cid = int(row[0]) if row else None
            if cid is None:
                log.warning(
                    "sorting_intake_sources id=%s owner_name=%s: no matching character; "
                    "leaving owner_name, not deleting",
                    source_id, owner_name,
                )
                unmatched.append(int(source_id))
                continue
            conn.execute(
                "UPDATE sorting_intake_sources SET owner_character_id = ? WHERE id = ?",
                (cid, source_id),
            )
        elif source_kind == "corp":
            if existing_corp is not None:
                continue
            row = conn.execute(
                "SELECT owner_corporation_id FROM corp_assets "
                "WHERE owner_name = ? AND owner_corporation_id IS NOT NULL LIMIT 1",
                (owner_name,),
            ).fetchone()
            corp_id = int(row[0]) if row else None
            if corp_id is None:
                log.warning(
                    "sorting_intake_sources id=%s owner_name=%s: no matching corporation id; "
                    "leaving owner_name, not deleting",
                    source_id, owner_name,
                )
                unmatched.append(int(source_id))
                continue
            conn.execute(
                "UPDATE sorting_intake_sources SET owner_corporation_id = ? WHERE id = ?",
                (corp_id, source_id),
            )
    return unmatched

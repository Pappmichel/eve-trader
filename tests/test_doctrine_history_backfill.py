"""Doctrine sales history gaps are filled from the contract archive
(doctrine/esi_sync.backfill_history_from_archive)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from eve_trader.doctrine import esi_sync
from eve_trader.doctrine.config import DoctrineConfig
from eve_trader.realized import access as realized_access
from eve_trader.realized import db as realized_db
from eve_trader.realized import loader as realized_loader

CHAR, CORP, BUYER = 1234567895, 98000001, 1234567897
LOC = 1049588174021
DONE = datetime(2026, 11, 13, 22, 37, tzinfo=timezone.utc)


def test_missing_doctrine_sales_are_added_from_the_archive(monkeypatch):
    rows = [
        # (contract_id, issuer, acceptor, title, price, issued, completed)
        (1, CHAR, BUYER, "GSF - Tomahawk", 260e6, DONE, DONE),     # missing, doctrine fit -> added
        (2, CHAR, BUYER, "GSF - Tomahawk", 260e6, DONE, DONE),     # already in history
        (3, CHAR, CORP, "to the corp", 0.0, DONE, DONE),           # internal transfer
        (4, 555, BUYER, "member's own", 1e6, DONE, DONE),          # not issued by the tenant
        (5, CHAR, BUYER, "Fuel", 5e6, DONE, DONE),                 # not a fit
    ]
    monkeypatch.setattr(realized_access, "shared_owners_for", lambda kind, tool: [("corporation", CORP)])
    seen_locations = []
    monkeypatch.setattr(realized_db, "finished_item_exchanges_at",
                        lambda owners, locs: seen_locations.append(sorted(locs)) or rows)
    monkeypatch.setattr(realized_db, "contract_items_by_id",
                        lambda owners, ids: {i: [(1, 4306, 1.0, True, True)] for i in ids})
    monkeypatch.setattr(esi_sync.storage, "doctrine_contract_history_ids", lambda ids: {2})
    monkeypatch.setattr(realized_loader, "_tenant_character_ids", lambda: frozenset({CHAR}))
    monkeypatch.setattr(realized_loader, "_tenant_corporation_ids", lambda: frozenset({CORP}))
    monkeypatch.setattr(esi_sync.engine, "load_match_candidates", lambda: [])
    matched_ids = []
    monkeypatch.setattr(esi_sync.engine, "match_and_validate_contract",
                        lambda cid, title, items, cands, cfg=None: matched_ids.append(cid) or (
                            ("fit-1", 1.0, [], "valid") if cid == 1 else (None, 0.0, [], "no_relevant_hull")))
    monkeypatch.setattr(esi_sync.storage, "get_fitting",
                        lambda fid: ("fit-1", "doc", "GSF - Tomahawk - DPS - V26.1", "eft", 4306))

    class _TM:
        def __init__(self, *a, **k):
            pass

        def list_records(self):
            return [SimpleNamespace(character_id=CHAR, role="doctrine:1234567895")]

    monkeypatch.setattr(esi_sync, "TokenManager", _TM)
    written = []
    monkeypatch.setattr(esi_sync.storage, "upsert_doctrine_contract_history", lambda r: written.extend(r))
    client = SimpleNamespace(resolve_names=lambda ids: {BUYER: "Buyer"})

    cfg = DoctrineConfig(doctrine_structure_ids=[LOC])
    assert esi_sync.backfill_history_from_archive(cfg, client=client) == 1

    assert seen_locations == [[LOC]]
    assert matched_ids == [1, 5]
    assert written == [(1, "doctrine:1234567895", "fit-1", "GSF - Tomahawk - DPS - V26.1", 4306,
                        "GSF - Tomahawk", 260e6, BUYER, "Buyer", DONE, DONE)]

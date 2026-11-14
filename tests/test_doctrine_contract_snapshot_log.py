"""Why a contract leaves an owner's doctrine snapshot or loses its match
(esi_data/fetchers.py). A dropped contract returns unmatched on the next
sync; the log names the cause."""
from __future__ import annotations

import logging

from eve_trader.esi_data import fetchers

CORP = 98_000_001
LOC = 1_049_588_174_021


def _raw(cid, status="outstanding", loc=LOC):
    return {"contract_id": cid, "type": "item_exchange", "status": status, "start_location_id": loc,
            "issuer_id": 1, "title": "t", "price": 1.0, "date_expired": None}


def _existing(cid, status="outstanding", score=1.0):
    # _CONTRACT_COLUMNS order
    return (cid, "doctrine:1", True, 1, LOC, status, "t", 1.0, None,
            "fit" if score is not None else None, score, "valid" if score is not None else "unmatched", None)


def test_dropped_and_rematched_contracts_are_logged(monkeypatch, caplog):
    snapshot = [_existing(1), _existing(2), _existing(3), _existing(4), _existing(5, status="in_progress")]
    monkeypatch.setattr(fetchers.storage, "load_doctrine_contracts", lambda: snapshot)
    monkeypatch.setattr(fetchers.storage, "doctrine_contract_ids_for_owner", lambda ot, oid: {1, 2, 3, 4, 5})
    written = {}
    monkeypatch.setattr(fetchers.storage, "replace_doctrine_sync_snapshot",
                        lambda rows, items, devs, **kw: written.update(rows=rows))
    syncable = [_raw(1), _raw(2), _raw(5)]
    all_raw = syncable + [_raw(3, status="finished"), _raw(4, loc=60003760)]
    items_info = {"carried": {1: [], 5: []}, "fetched": {}, "errors": {2: "HTTP 420"}}

    with caplog.at_level(logging.INFO, logger=fetchers.log.name):
        fetchers._write_contract_snapshot(
            syncable, items_info, owner_type="corporation", owner_id=CORP, auth_role="doctrine:1",
            for_corp=True, all_raw=all_raw, structure_ids={LOC},
        )

    assert [r[0] for r in written["rows"]] == [1, 5]
    text = caplog.text
    assert "2 (items read failed: HTTP 420)" in text
    assert "3 (status finished, type item_exchange)" in text
    assert "4 (outside structure filter (location 60003760))" in text
    assert "5 (status in_progress -> outstanding)" in text


def test_nothing_logged_when_the_snapshot_is_unchanged(monkeypatch, caplog):
    monkeypatch.setattr(fetchers.storage, "load_doctrine_contracts", lambda: [_existing(1)])
    monkeypatch.setattr(fetchers.storage, "doctrine_contract_ids_for_owner", lambda ot, oid: {1})
    monkeypatch.setattr(fetchers.storage, "replace_doctrine_sync_snapshot", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger=fetchers.log.name):
        fetchers._write_contract_snapshot(
            [_raw(1)], {"carried": {1: []}, "fetched": {}, "errors": {}},
            owner_type="corporation", owner_id=CORP, auth_role="doctrine:1", for_corp=True,
        )
    assert "doctrine contracts" not in caplog.text

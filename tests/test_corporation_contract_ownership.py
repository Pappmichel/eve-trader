"""A corporation owns only contracts issued on its behalf.

issuer_corporation_id is the issuer's corporation on every contract, also on a
member's personal one. Without for_corporation a shared corporation would take
in every member's contracts as its own.
"""
from __future__ import annotations

from eve_trader.esi_data import fetchers
from eve_trader.realized import ingest

CORP = 98_000_001
MEMBER = 2_000_001
OUTSIDER = 2_000_002


def _contract(contract_id=1, *, for_corporation, issuer_corp=CORP, acceptor=0, **extra):
    return {
        "contract_id": contract_id, "type": "item_exchange", "status": "outstanding",
        "issuer_id": MEMBER, "issuer_corporation_id": issuer_corp,
        "for_corporation": for_corporation, "acceptor_id": acceptor,
        "start_location_id": 60003760, **extra,
    }


def test_corporation_issued_only_with_for_corporation():
    match = ingest.contract_issuer_matches
    assert match(_contract(for_corporation=True), owner_type="corporation", owner_id=CORP)
    assert not match(_contract(for_corporation=False), owner_type="corporation", owner_id=CORP)


def test_missing_for_corporation_counts_as_personal():
    contract = _contract(for_corporation=False)
    del contract["for_corporation"]
    assert not ingest.contract_issuer_matches(contract, owner_type="corporation", owner_id=CORP)


def test_other_corporations_contract_never_matches():
    contract = _contract(for_corporation=True, issuer_corp=98_000_002)
    assert not ingest.contract_issuer_matches(contract, owner_type="corporation", owner_id=CORP)


def test_character_issuer_ignores_for_corporation():
    for flag in (True, False):
        contract = _contract(for_corporation=flag)
        assert ingest.contract_issuer_matches(contract, owner_type="character", owner_id=MEMBER)
        assert not ingest.contract_issuer_matches(contract, owner_type="character", owner_id=OUTSIDER)


def test_party_match_for_corporation():
    match = ingest.contract_party_matches
    assert match(_contract(for_corporation=True), owner_type="corporation", owner_id=CORP)
    # A member's personal contract is not the corporation's...
    assert not match(_contract(for_corporation=False), owner_type="corporation", owner_id=CORP)
    # ...but a contract the corporation accepted stays its own.
    assert match(_contract(for_corporation=False, issuer_corp=98_000_002, acceptor=CORP),
                 owner_type="corporation", owner_id=CORP)


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.item_reads = []

    def corporation_contracts(self, corporation_id, auth_role):
        return self.rows

    def corporation_contract_items(self, corporation_id, contract_id, auth_role):
        self.item_reads.append(contract_id)
        return []


def test_corporation_fetcher_keeps_only_its_own_contracts(monkeypatch):
    """Doctrine's snapshot, its finished history and the ledger pass all start
    from the same list."""
    written, finished_seen, archived = [], [], []
    monkeypatch.setattr(fetchers, "_fetch_contract_items_for",
                        lambda client, entries, **k: {"carried": {}, "fetched": {}, "errors": {},
                                                      "entries": entries})
    monkeypatch.setattr(fetchers, "_record_finished_history",
                        lambda client, finished, **k: finished_seen.extend(finished) or 0)
    monkeypatch.setattr(fetchers, "_write_contract_snapshot",
                        lambda syncable, items_info, **k: written.extend(syncable) or len(syncable))
    monkeypatch.setattr(fetchers, "_archive_contracts_pass",
                        lambda client, **k: archived.extend(k["raw_list"]))

    rows = [
        _contract(1, for_corporation=True),
        _contract(2, for_corporation=False),
        _contract(3, for_corporation=True, status="finished"),
        _contract(4, for_corporation=False, status="finished"),
    ]
    fetchers.fetch_corporation_contracts(_Client(rows), CORP, "esi:1", "Corp")

    assert [c["contract_id"] for c in written] == [1]
    assert [c["contract_id"] for c in finished_seen] == [3]
    # The ledger pass gets the raw list and filters by itself (contract_party_matches).
    assert len(archived) == 4


def test_archive_pass_reads_items_only_for_own_contracts(monkeypatch):
    """The expensive part: a member's personal contract must not cost an item read."""
    from eve_trader.realized import db as ledger_db

    entries_seen = []

    def fake_fetch(client, entries, **k):
        entries_seen.extend(entries)
        return {"carried": {}, "fetched": {}, "errors": {}}

    monkeypatch.setattr(fetchers, "_archive_for_production", lambda *a: True)
    monkeypatch.setattr(fetchers, "_shared_with_portfolio", lambda *a: False)
    monkeypatch.setattr(fetchers, "_fetch_contract_items_for", fake_fetch)
    monkeypatch.setattr(ledger_db, "contract_ids_with_items", lambda *a: set())
    archived = {}
    monkeypatch.setattr(ingest, "archive_contracts",
                        lambda **k: archived.update(k) or {"contracts": 0, "items": 0})

    fetchers._archive_contracts_pass(
        object(), owner_type="corporation", owner_id=CORP, auth_role="esi:1",
        raw_list=[
            _contract(1, for_corporation=True),
            _contract(2, for_corporation=False),
            _contract(3, for_corporation=False, issuer_corp=98_000_002, acceptor=CORP),
            _contract(4, for_corporation=False, type="courier", status="finished"),
            _contract(5, for_corporation=True, type="courier", status="finished"),
        ],
    )

    assert sorted(entry[0]["contract_id"] for entry in entries_seen) == [1, 3]
    assert sorted(c["contract_id"] for c in archived["contracts"]) == [1, 3, 5]

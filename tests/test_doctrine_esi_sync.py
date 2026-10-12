"""Unit tests for doctrine/esi_sync.py's pure filtering helpers -
_passes_prefilter and _issued_by_own_identity. sync_contracts/sync_assets
themselves (real ESI/storage orchestration) have no dedicated unit test
file, consistent with how production's own sync_esi is only exercised via
router-level/live testing, not unit-mocked step by step.
"""
from eve_trader.doctrine import esi_sync

STRUCTURE_ID = 1000000000001


def _contract(**overrides) -> dict:
    base = {
        "type": "item_exchange", "status": "outstanding", "start_location_id": STRUCTURE_ID,
        "issuer_id": 42, "issuer_corporation_id": 500,
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------- _passes_prefilter
def test_passes_prefilter_accepts_outstanding_item_exchange_at_structure():
    assert esi_sync._passes_prefilter(_contract(), STRUCTURE_ID) is True


def test_passes_prefilter_accepts_expired_status_now():
    # Widened for issue #3c - an expired contract must still be synced (so
    # it can be shown with an Expired badge instead of silently vanishing).
    assert esi_sync._passes_prefilter(_contract(status="expired"), STRUCTURE_ID) is True


def test_passes_prefilter_rejects_wrong_type():
    assert esi_sync._passes_prefilter(_contract(type="courier"), STRUCTURE_ID) is False


def test_passes_prefilter_rejects_unsyncable_status():
    assert esi_sync._passes_prefilter(_contract(status="cancelled"), STRUCTURE_ID) is False


def test_passes_prefilter_rejects_wrong_location():
    assert esi_sync._passes_prefilter(_contract(start_location_id=999), STRUCTURE_ID) is False


def test_passes_prefilter_rejects_when_structure_not_configured():
    assert esi_sync._passes_prefilter(_contract(), None) is False


# ------------------------------------------------------------- _passes_history_filter
def test_passes_history_filter_accepts_finished_item_exchange_at_structure():
    # GitHub issue #19 - the whole point of this filter existing separately
    # from _passes_prefilter: "finished" is deliberately excluded from
    # SYNCABLE_CONTRACT_STATUSES (a finished contract has nothing left to
    # validate/match against), but must still be caught here before it drops
    # out of the active snapshot with no record left anywhere.
    assert esi_sync._passes_history_filter(_contract(status="finished"), STRUCTURE_ID) is True


def test_passes_history_filter_accepts_finished_issuer_and_contractor_variants():
    assert esi_sync._passes_history_filter(_contract(status="finished_issuer"), STRUCTURE_ID) is True
    assert esi_sync._passes_history_filter(_contract(status="finished_contractor"), STRUCTURE_ID) is True


def test_passes_history_filter_rejects_still_outstanding():
    # The inverse of _passes_prefilter's own acceptance of "outstanding" -
    # an outstanding contract isn't history yet, it's still active.
    assert esi_sync._passes_history_filter(_contract(status="outstanding"), STRUCTURE_ID) is False


def test_passes_history_filter_rejects_wrong_type():
    assert esi_sync._passes_history_filter(_contract(type="courier", status="finished"), STRUCTURE_ID) is False


def test_passes_history_filter_rejects_wrong_location():
    assert esi_sync._passes_history_filter(
        _contract(status="finished", start_location_id=999), STRUCTURE_ID) is False


def test_passes_history_filter_rejects_when_structure_not_configured():
    assert esi_sync._passes_history_filter(_contract(status="finished"), None) is False


# ------------------------------------------------------------- _issued_by_own_identity
def test_issued_by_own_identity_matches_on_issuer_character_id():
    contract = _contract(issuer_id=42, issuer_corporation_id=999)
    assert esi_sync._issued_by_own_identity(contract, character_id=42, corporation_id=500) is True


def test_issued_by_own_identity_matches_on_issuer_corporation_id():
    contract = _contract(issuer_id=999, issuer_corporation_id=500)
    assert esi_sync._issued_by_own_identity(contract, character_id=42, corporation_id=500) is True


def test_issued_by_own_identity_rejects_third_party_contract_merely_assigned():
    # Confirmed via ESIClient.character_contracts' own docstring: the
    # character-contracts endpoint returns issuer/acceptor/assignee
    # contracts - a contract issued by a totally unrelated party but merely
    # assigned to this character/corp must not pass.
    contract = _contract(issuer_id=999, issuer_corporation_id=888)
    assert esi_sync._issued_by_own_identity(contract, character_id=42, corporation_id=500) is False


def test_issued_by_own_identity_false_when_corporation_id_unknown():
    contract = _contract(issuer_id=999, issuer_corporation_id=500)
    assert esi_sync._issued_by_own_identity(contract, character_id=42, corporation_id=None) is False


# ------------------------------------------------------------- finished-contract history
class _FakeClient:
    def __init__(self, items=None):
        self.items = items or {}
        self.item_calls = []

    def resolve_names(self, ids):
        return {i: f"name{i}" for i in ids}

    def character_contract_items(self, character_id, contract_id, auth_role):
        self.item_calls.append(("character", character_id, contract_id))
        return self.items[contract_id]

    def corporation_contract_items(self, corporation_id, contract_id, auth_role):
        self.item_calls.append(("corporation", corporation_id, contract_id))
        return self.items[contract_id]


def _finished(cid, **overrides):
    return _contract(contract_id=cid, status="finished", title="t", price=1.0, acceptor_id=7,
                     date_issued="2026-10-10T00:00:00Z", date_completed="2026-10-11T00:00:00Z", **overrides)


def test_fetcher_records_history_for_matched_snapshot_contract_before_replace(monkeypatch):
    """The scheduler's contracts sync replaces the snapshot without the
    finished contract; its fitting match must be recorded first."""
    from eve_trader import storage
    from eve_trader.esi_data import fetchers

    snapshot_row = (1, "role", False, 42, STRUCTURE_ID, "outstanding", "t", 1.0, None, "fit-1", 1.0, "valid", "x")
    written = []
    monkeypatch.setattr(storage, "doctrine_contract_history_ids", lambda ids: {3})
    monkeypatch.setattr(storage, "load_doctrine_contracts",
                        lambda: [snapshot_row, (2, *snapshot_row[1:9], None, None, "unmatched", "x"),
                                 (3, *snapshot_row[1:])])
    monkeypatch.setattr(storage, "get_fitting", lambda fid: (fid, "doc", "Fit Name", None, 999))
    monkeypatch.setattr(storage, "upsert_doctrine_contract_history", lambda rows: written.extend(rows))

    n = fetchers._record_finished_history(_FakeClient(), [_finished(1), _finished(2), _finished(3)],
                                          auth_role="role")
    assert n == 1
    assert written == [(1, "role", "fit-1", "Fit Name", 999, "t", 1.0, 7, "name7",
                        "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z")]


def test_doctrine_sync_matches_finished_contract_missing_from_snapshot(monkeypatch):
    """A finished contract whose snapshot row is already gone (or that was
    never matched) is matched from its ESI items; recorded ones are skipped."""
    from eve_trader import storage

    client = _FakeClient(items={10: [{"record_id": 1, "type_id": 999, "quantity": 1}]})
    written = []
    monkeypatch.setattr(esi_sync, "ESIClient", lambda tokens=None: client)
    monkeypatch.setattr(esi_sync.engine, "load_match_candidates", lambda: [])
    monkeypatch.setattr(esi_sync.engine, "match_and_validate_contract",
                        lambda cid, title, items, cands, cfg: ("fit-1", 1.0, [], "valid"))
    monkeypatch.setattr(storage, "doctrine_contract_history_ids", lambda ids: {11})
    monkeypatch.setattr(storage, "get_fitting", lambda fid: (fid, "doc", "Fit Name", None, 999))
    monkeypatch.setattr(storage, "upsert_doctrine_contract_history", lambda rows: written.extend(rows))

    finished = {
        10: (_finished(10), [("character", 42, "char-role"), ("corporation", 500, "corp-role")]),
        11: (_finished(11), [("character", 42, "char-role")]),
    }
    assert esi_sync._record_unmatched_finished(finished, tm=None) == 1
    assert client.item_calls == [("corporation", 500, 10)]
    assert [(r[0], r[1], r[2], r[8]) for r in written] == [(10, "corp-role", "fit-1", "name7")]

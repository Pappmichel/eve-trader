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

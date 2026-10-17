"""Production ledger: decryptor derivation, ME fallbacks and the fetchers'
share gate (docs/PRODUCTION_REALIZED_PROFIT_PLAN.md, Phase 0 V8 / 13b).
Pure - storage and ESI are monkeypatched, no Postgres needed."""
from __future__ import annotations

import pytest

from eve_trader.esi_data import fetchers
from eve_trader.production.config import ProductionConfig
from eve_trader.production.constants import DECRYPTORS
from eve_trader.production.realized import db as ledger_db
from eve_trader.production.realized import ingest as ledger_ingest
from eve_trader.production.realized import inputs

T1_BLUEPRINT = 1000
T2_BLUEPRINT = 2000
T2_ITEM = 777          # what T2_BLUEPRINT manufactures; selected_decryptors key
ENC, SCI1, SCI2 = 11, 12, 13
BASE = 0.30
# Installer with all three skills at 5: 1 + 5/40 + (5 + 5)/30
FACTOR = 1 + 5 / 40 + 10 / 30


@pytest.fixture
def invention_env(monkeypatch):
    monkeypatch.setattr(inputs.storage, "get_blueprint_skills",
                        lambda bp, activity: [(ENC, 1), (SCI1, 1), (SCI2, 1)])
    monkeypatch.setattr(inputs, "_is_encryption_skill", lambda sid: sid == ENC)
    monkeypatch.setattr(inputs, "_installer_skill_levels",
                        lambda installer, ids: {ENC: 5, SCI1: 5, SCI2: 5})
    monkeypatch.setattr(inputs.storage, "get_invention_recipe",
                        lambda bp, product=None: {"base_probability": BASE})
    monkeypatch.setattr(ledger_db, "manufactured_product_of",
                        lambda bp: T2_ITEM if bp == T2_BLUEPRINT else None)


def _match(probability, selected=None):
    return inputs._match_decryptor(probability, T1_BLUEPRINT, T2_BLUEPRINT, 1, selected or {})


def _name(type_id):
    return next(n for n, d in DECRYPTORS.items() if (d.type_id or None) == type_id)


def test_exact_probability_match_names_the_decryptor(invention_env):
    dec, source, enc, sci1, sci2 = _match(BASE * FACTOR * 1.5)
    assert (_name(dec), source, (enc, sci1, sci2)) == ("Parity", "probability_decryptor", (5, 5, 5))


def test_none_or_symmetry_uses_the_selection_keyed_by_the_t2_item(invention_env):
    prob = BASE * FACTOR * 1.0
    assert _match(prob)[:2] == (None, "assumed_decryptor")
    dec, source, *_ = _match(prob, {T2_ITEM: "Symmetry"})
    assert (_name(dec), source) == ("Symmetry", "probability_decryptor")
    # Keyed by the T2 *blueprint* (the old bug) must not be found.
    assert _match(prob, {T2_BLUEPRINT: "Symmetry"})[:2] == (None, "assumed_decryptor")
    # A selection that contradicts the probability is not trusted.
    assert _match(prob, {T2_ITEM: "Parity"})[:2] == (None, "assumed_decryptor")


def test_missing_skills_never_produce_a_probability_match(invention_env, monkeypatch):
    """Phase 1 review: with a guessed skill factor of 1.0 and a 0.05 band,
    64 real None/Symmetry jobs came out as 'Parity' (probability_decryptor).
    Without real skill levels the decryptor is always assumed."""
    monkeypatch.setattr(inputs, "_installer_skill_levels", lambda installer, ids: {})
    prob = BASE * FACTOR * 1.0   # implied 1.458 under a guessed factor of 1
    assert _match(prob)[:2] == (None, "assumed_decryptor")
    dec, source, *_ = _match(prob, {T2_ITEM: "Process"})
    assert (_name(dec), source) == ("Process", "assumed_decryptor")


def test_near_miss_and_capped_probability_are_assumed(invention_env):
    assert _match(BASE * FACTOR * 1.43)[1] == "assumed_decryptor"   # no 0.05 band
    assert _match(1.0)[1] == "assumed_decryptor"                    # min(1, ...) cap


def test_unknown_skill_shape_is_assumed(invention_env, monkeypatch):
    monkeypatch.setattr(inputs.storage, "get_blueprint_skills",
                        lambda bp, activity: [(SCI1, 1), (SCI2, 1)])
    assert _match(BASE * FACTOR * 1.5)[1] == "assumed_decryptor"


@pytest.fixture
def no_blueprint_seen(monkeypatch):
    monkeypatch.setattr(ledger_db, "lookup_blueprint_me_te", lambda item_id: None)
    monkeypatch.setattr(ledger_db, "lookup_manual_blueprint_me", lambda bp: None)


def test_tech_ii_me_falls_back_to_the_decryptor_me(no_blueprint_seen):
    cfg = ProductionConfig()
    assert inputs._resolve_blueprint_me(5, T2_BLUEPRINT, "Tech II", cfg, product_type_id=T2_ITEM,
                                        selected_decryptors={T2_ITEM: "Accelerant"}) == (4, "assumed_me")
    # No selection: the no-decryptor invention baseline ME 2, not ME 0.
    assert inputs._resolve_blueprint_me(5, T2_BLUEPRINT, "Tech II", cfg,
                                        product_type_id=T2_ITEM) == (2, "assumed_me")


def test_other_me_fallbacks(no_blueprint_seen):
    cfg = ProductionConfig()
    assert inputs._resolve_blueprint_me(5, 1, "Tech I", cfg) == (10, "assumed_me")
    assert inputs._resolve_blueprint_me(5, 1, "Faction", cfg) == (0, "assumed_me")
    assert inputs._resolve_blueprint_me(5, 1, "Reaction", cfg) == (0, "assumed_me")


# --------------------------------------------------------- fetchers' share gate
def test_archive_is_skipped_when_the_kind_is_not_shared_with_production(monkeypatch):
    calls = []
    monkeypatch.setattr(ledger_ingest, "archive_wallet", lambda **kw: calls.append(kw))
    monkeypatch.setattr(fetchers, "_archive_for_production", lambda kind, ot, oid: False)
    fetchers._archive_wallet("character", 1, [], [])
    assert calls == []
    monkeypatch.setattr(fetchers, "_archive_for_production", lambda kind, ot, oid: kind == "wallet")
    fetchers._archive_wallet("character", 1, [], [])
    assert len(calls) == 1


def test_contract_items_are_fetched_once_per_contract(monkeypatch):
    monkeypatch.setattr(fetchers, "_archive_for_production", lambda kind, ot, oid: True)
    monkeypatch.setattr(ledger_db, "contract_ids_with_items", lambda ot, oid, ids: {1})
    asked = []

    def fetch_items(client, entries, *, character_id, auth_role):
        asked.extend(raw["contract_id"] for raw, _corp, _cid in entries)
        return {"carried": {}, "fetched": {}, "errors": {}}

    archived = []
    monkeypatch.setattr(fetchers, "_fetch_contract_items_for", fetch_items)
    monkeypatch.setattr(ledger_ingest, "archive_contracts", lambda **kw: archived.append(kw))
    raw = [
        {"contract_id": 1, "type": "item_exchange", "issuer_id": 5, "status": "finished"},
        {"contract_id": 2, "type": "item_exchange", "issuer_id": 5, "status": "outstanding"},
        {"contract_id": 3, "type": "item_exchange", "issuer_id": 6, "status": "finished"},
        {"contract_id": 4, "type": "courier", "issuer_id": 5, "status": "finished"},
    ]
    fetchers._archive_contracts_pass(object(), owner_type="character", owner_id=5,
                                     auth_role="r", raw_list=raw)
    assert asked == [2]   # 1 already has items, 3 is someone else's, 4 is courier
    assert [c["contract_id"] for c in archived[0]["contracts"]] == [1, 2]

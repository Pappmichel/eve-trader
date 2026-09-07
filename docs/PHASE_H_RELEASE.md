# Phase H — Release candidate (online Special-Order path)

Certification record for the Production Special-Order path after Phases C–G
on **eve-trader** (FastAPI + Postgres + React). Core stays frozen
(`docs/PRODUCTION_SEMANTICS.md`).

**Identifier:** package `0.2.0rc1`, git tag `v0.2.0-rc1`  
**Machine:** Cloud Agent VM, Python 3.12, local Postgres.

This certifies the online Special-Order path only. It is not a Trading /
Doctrine / Ore release. The local desktop tool has its own `0.4.0rc1`.

## H.1 — Full regression

Recorded 2026-09-07:

| Command | Result |
|---------|--------|
| `pytest -m release` | 62 passed |
| `pytest` (full suite) | 1141 passed |

Release-marker split:

| Phase | File | Tests |
|-------|------|-------|
| C | `tests/test_phase_c_adversarial_audit.py` | 22 |
| D | `tests/test_phase_d_release_acceptance.py` | 5 |
| E.2 | `tests/test_phase_e2_order_persistence.py` | 7 |
| E.3 | `tests/test_phase_e3_ux.py` | 3 |
| E.4 | `tests/test_phase_e4_auto_recompute.py` | 5 |
| E.5 | `tests/test_phase_e5_production_features.py` | 2 |
| F.1 | `tests/test_phase_f1_cross_feature.py` | 4 |
| F.2 | `tests/test_phase_f2_end_to_end.py` | 4 |
| G.1 | `tests/test_phase_g1_scale_baseline.py` | 5 |
| G.2 | `tests/test_phase_g2_reliability.py` | 1 |
| G.3 | `tests/test_phase_g3_failure_recovery.py` | 4 |

Also green in the same session: production engine/invention, special-order
actions, FastAPI router tests, storage special-orders, sqlite-migration
table-drift (includes `special_order_events`).

## H.2 — G.1 re-run

Second `pytest tests/test_phase_g1_scale_baseline.py -s` on this branch.
Ranking unchanged vs the G.1 table in `docs/PHASE_G_SCALE.md`: create × N
still dominates; Combined stays one planner call; list still N+1
item_count queries. Semantic gates still pass.

## H.3 — Integrity / reliability

G.2 (20 edit/recompute cycles) and G.3 (rollback / audit after failure)
passed as part of `pytest -m release`. Tenant isolation of
`special_order_events` is covered in E.2.

## H.4 — Notes and limitations

See `docs/RELEASE_NOTES.md` and `docs/KNOWN_LIMITATIONS.md`.

## H.5 — Versioning

See `docs/VERSIONING.md`. After the tag: no production-code changes on this
identifier. Further work is a new version.

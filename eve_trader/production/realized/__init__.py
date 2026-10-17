"""Production realized profit ledger (docs/PRODUCTION_REALIZED_PROFIT_PLAN.md).

Phase 1: append-only ESI archive (`ingest`, `inputs`, `db`).
Phase 2: pure `costing` + thin `loader` that rebuilds derived tables.
Phase 3+: report / API / CLI.
"""

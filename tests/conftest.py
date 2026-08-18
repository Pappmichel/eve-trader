"""Project-wide pytest fixtures. Registers tests/pg_helpers.py's `tenant_pair`
fixture project-wide - it's pure in-memory `uuid.uuid4()`, zero I/O, so
sharing it here costs nothing even for the ~300+ non-Postgres tests that
never use it.

Deliberately does NOT also register `_apply_phase1_schema` here (it did,
briefly, in an earlier version of this session's work) - that fixture opens
a real network connection to check Postgres reachability, and a
conftest.py-registered session-autouse fixture runs for *every* `pytest`
invocation under tests/, not just the Postgres-specific ones. Confirmed
live: with the local dev Postgres stopped, that cost even a single-file,
Postgres-unrelated run (`pytest tests/test_shortlist.py`) an extra ~4-5s of
pure connection-timeout overhead it has no reason to pay. Each Postgres
test module (test_pg_tenant_isolation.py etc.) now imports
`_apply_phase1_schema` itself instead, so the cost is scoped to exactly the
runs that need it - see tests/pg_helpers.py for the fixture itself.
"""
from __future__ import annotations

from .pg_helpers import tenant_pair  # noqa: F401

# Note on storage.py's @lru_cache'd SDE-lookup functions (get_sde_type,
# get_system_security, ...): before the multi-tenant cutover, `db_path` was
# part of every one of those signatures, so each test's own fresh SQLite
# file gave every test an isolated cache-key space for free. Postgres has
# no per-test file to piggyback on, so these caches are now genuinely
# global for the whole `pytest` process - a blanket "clear all of them
# before every test" fixture was tried here and reverted: it forced *every*
# test that transitively reaches a cached lookup (e.g. any
# production/engine.py cost calculation, via
# _security_multiplier_for -> get_system_security) through a real,
# uncached Postgres connection, even tests that fully monkeypatch
# storage.* and previously never touched a real connection at all -
# confirmed live, this alone turned ~18 previously Postgres-independent
# tests in test_production_engine.py into ones requiring it. The actual
# staleness risk this was meant to guard against is narrow in practice:
# replace_sde_data() (the one function tests use to seed fresh SDE data)
# already calls .cache_clear() on all of them itself, and the SDE-seeding
# tests that bypass it (test_storage_sde_queries.py) use non-overlapping
# type_ids per test. If a genuinely stale-cache test failure shows up later,
# fix it at the call site (explicit .cache_clear() in that test's own
# setup) rather than reintroducing a blanket fixture.

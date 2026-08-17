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

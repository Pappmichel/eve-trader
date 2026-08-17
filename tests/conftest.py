"""Project-wide pytest fixtures. Currently just registers the Postgres
multi-tenant migration's fixtures (see tests/pg_helpers.py) so every test
module under tests/ can use them without its own import - `_apply_phase1_schema`
is session-scoped autouse (no-ops instantly if the local dev Postgres isn't
reachable), `tenant_pair` is a regular per-test fixture.
"""
from __future__ import annotations

from .pg_helpers import _apply_phase1_schema, tenant_pair  # noqa: F401

"""Postgres connection layer for the multi-tenant migration (see
docs/MULTI_TENANT_PLAN.md) - Phase 0 proof of concept, not yet wired into the
live app. This is what storage.py's connect()/batch_session() will be ported
to once every table has been migrated (Phase 1); kept as its own module until
then so the still-SQLite-backed rest of storage.py isn't broken mid-migration.

Tenant isolation is enforced by Postgres Row-Level Security (RLS), not by
filtering queries here - every per-tenant table's RLS policy compares its
tenant_id column against the `app.tenant_id` session setting, which this
module sets via `set_config('app.tenant_id', ..., true)` (the parameterized
equivalent of `SET LOCAL` - plain `SET LOCAL x = %s` doesn't accept a bound
parameter, confirmed live) as the first statement of every transaction. If
that's ever skipped, Postgres itself raises `unrecognized configuration
parameter "app.tenant_id"` on the first query - confirmed live: this fails
loudly, not by silently returning every tenant's rows.

The app connects as a non-owner, non-BYPASSRLS role (`eve_trader_app` in dev -
see docs/MULTI_TENANT_PLAN.md for why the table owner must be a separate
role); RLS is silently skipped for a table's owner/superuser by default.
"""
from __future__ import annotations

import contextvars
import os
import re
from contextlib import contextmanager
from typing import Optional

from psycopg_pool import ConnectionPool

# Dev defaults match the local `eve-trader-pg` Docker container (see
# docs/MULTI_TENANT_PLAN.md's Phase 0) - override via env vars for any other
# environment. Never a hardcoded production credential.
PG_DSN = os.getenv(
    "EVE_TRADER_PG_DSN",
    "host=localhost port=5432 dbname=eve_trader user=eve_trader_app password=app_devpassword",
)

_tenant_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tenant_id", default=None)

_pool: Optional[ConnectionPool] = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(PG_DSN, min_size=1, max_size=10, open=True)
    return _pool


def set_current_tenant(tenant_id: Optional[str]) -> contextvars.Token:
    """Sets the tenant for the current context (a request, or a `with` block
    in a test) - returns a Token for `reset_current_tenant`. Call this once,
    e.g. from AccessGateMiddleware after validating the session cookie (once
    this module replaces storage.py's connect()) - individual storage
    functions never need to know about tenant_id themselves."""
    return _tenant_id_var.set(tenant_id)


def reset_current_tenant(token: contextvars.Token) -> None:
    _tenant_id_var.reset(token)


@contextmanager
def tenant_context(tenant_id: str):
    """Convenience wrapper for tests/scripts: `with tenant_context(tid): ...`"""
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


# Matches a `?` placeholder outside of single-quoted string literals - a
# naive str.replace("?", "%s") would also corrupt a literal "?" appearing
# inside quoted SQL text (e.g. a LIKE pattern). None of storage.py's existing
# queries do that (confirmed by inspection during the Plan-agent review), but
# this regex is defensive rather than relying on that staying true forever -
# it skips over '...'-quoted spans (including doubled '' escapes) untouched.
_PLACEHOLDER_RE = re.compile(r"'(?:[^']|'')*'|(\?)")


def translate_placeholders(sql: str) -> str:
    """SQLite's `?` positional placeholder -> psycopg's `%s` - lets every
    existing query string in storage.py port to Postgres without individually
    rewriting each one's placeholder style."""
    return _PLACEHOLDER_RE.sub(lambda m: "%s" if m.group(1) else m.group(0), sql)


class _TranslatingCursor:
    """Wraps a psycopg cursor so `execute`/`executemany` transparently
    translate `?`-style SQL - existing storage.py call sites (`conn.execute(
    "... WHERE type_id = ?", (type_id,))`) work unchanged once connect()
    hands out a connection whose `.execute` goes through this."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=None):
        return self._cursor.execute(translate_placeholders(sql), params)

    def executemany(self, sql, params_seq):
        return self._cursor.executemany(translate_placeholders(sql), params_seq)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)


class _TranslatingConnection:
    """Same idea as _TranslatingCursor, one level up - storage.py calls
    `conn.execute(...)` directly on the connection (sqlite3.Connection's own
    shorthand) in several places, not just `conn.cursor().execute(...)`."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(translate_placeholders(sql), params)
        return _TranslatingCursor(cur)

    def cursor(self):
        return _TranslatingCursor(self._conn.cursor())

    def __getattr__(self, name):
        return getattr(self._conn, name)


@contextmanager
def connect():
    """Postgres equivalent of storage.py's `connect()` - checks out a pooled
    connection, sets `app.tenant_id` for this transaction from the current
    context (raises RuntimeError if no tenant is set, rather than silently
    running un-scoped), yields a connection whose `.execute` accepts the same
    `?`-placeholder SQL the rest of storage.py already writes."""
    tenant_id = _tenant_id_var.get()
    if tenant_id is None:
        raise RuntimeError(
            "pg_tenant.connect() called with no current tenant set - "
            "call set_current_tenant()/tenant_context() first. Refusing to "
            "open a connection with no tenant scope rather than risk it "
            "defaulting to an un-scoped query."
        )
    pool = _get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            # `SET LOCAL x = %s` doesn't accept a bound parameter for the
            # value - Postgres's SET syntax isn't a regular query, psycopg
            # sends it as a prepared-statement parameter regardless and
            # Postgres rejects it (confirmed live: `syntax error at or near
            # "$1"`). set_config(name, value, is_local) is a plain function
            # call, takes a normal parameter, and with is_local=true is the
            # functional equivalent of SET LOCAL (reverts at COMMIT/ROLLBACK).
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        try:
            yield _TranslatingConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

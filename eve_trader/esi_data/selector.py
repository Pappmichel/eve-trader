"""Deterministic ESI token selector (docs/ESI_ACCESS_PLAN.md Phase 4).

A character may hold several `tenant_tokens` rows (a pool of legacy
prefix keys). Fetchers pick one token that carries the required scope,
never a prefix. Determinism is load-bearing: `ESIClient` caches on
`auth_role`, so an unstable choice for the same inputs splits that cache.

This module imports no tool package. Phase 6 calls `reauth_write_role`
and `delete_strict_subset_tokens`; nothing here writes an `esi:` key.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..auth import TokenManager, TokenRecord
from ..config import OAUTH_CONFIG

REAUTH_NEEDED = "re-auth needed (no token holds this scope)"


def normalize_scopes(scopes: str | Iterable[str]) -> frozenset[str]:
    """Split on whitespace, drop empties, dedupe. `"b a"` and `"a  b a"`
    are the same set.
    """
    if isinstance(scopes, str):
        parts = scopes.split()
    else:
        parts = [p for s in scopes for p in str(s).split()]
    return frozenset(p for p in parts if p)


def _records_for(
    character_id: int,
    *,
    records: Optional[Iterable[TokenRecord]],
    tokens: Optional[TokenManager],
) -> list[TokenRecord]:
    if records is not None:
        pool = list(records)
    else:
        tm = tokens or TokenManager(OAUTH_CONFIG)
        pool = tm.list_records()
    cid = int(character_id)
    return [r for r in pool if r.character_id == cid]


def select_auth_role(
    character_id: int,
    required_scope: str,
    *,
    records: Optional[Iterable[TokenRecord]] = None,
    tokens: Optional[TokenManager] = None,
) -> Optional[str]:
    """Role key for `character_id` that can satisfy `required_scope`.

    1. Filter to this character's tokens whose normalized scope set
       CONTAINS `required_scope`.
    2. Among those, pick the largest normalized set.
    3. Tie-break on the lexically first role key.

    Returns None when no token carries the scope — callers must not raise
    and must not fall back to a prefix listing.
    """
    holding: list[tuple[int, str]] = []
    for rec in _records_for(character_id, records=records, tokens=tokens):
        scopes = normalize_scopes(rec.scopes)
        if required_scope not in scopes:
            continue
        holding.append((len(scopes), rec.role))
    if not holding:
        return None
    # Largest set first; within that size, lexically first role.
    holding.sort(key=lambda item: (-item[0], item[1]))
    return holding[0][1]


def reauth_write_role(
    character_id: int,
    *,
    records: Optional[Iterable[TokenRecord]] = None,
    tokens: Optional[TokenManager] = None,
) -> str:
    """Role key a Characters re-auth should write to (Phase 6 caller).

    Reuse an existing key for this `character_id` if any (lexically first,
    same tie-break as the selector), else `esi:<id>`. Does not write.
    """
    roles = [r.role for r in _records_for(character_id, records=records, tokens=tokens)]
    if roles:
        return min(roles)
    return f"esi:{int(character_id)}"


def delete_strict_subset_tokens(
    character_id: int,
    *,
    tokens: Optional[TokenManager] = None,
    records: Optional[Iterable[TokenRecord]] = None,
) -> list[str]:
    """Delete other tokens of this character whose normalized scope set is
    a strict subset of another token of the same character.

    Equal sets are left alone — they collapse on the next re-auth, when
    the new superset makes both strict subsets. Phase 6 calls this after
    a successful superset write. Returns deleted role keys, sorted.
    """
    pool = _records_for(character_id, records=records, tokens=tokens)
    sets = {r.role: normalize_scopes(r.scopes) for r in pool}
    to_delete: list[str] = []
    for role, scopes in sets.items():
        if any(scopes < other for other_role, other in sets.items() if other_role != role):
            to_delete.append(role)
    if not to_delete:
        return []
    tm = tokens or TokenManager(OAUTH_CONFIG)
    for role in to_delete:
        tm.remove_token(role)
    return sorted(to_delete)


def character_has_token_pool(
    character_id: int,
    *,
    records: Optional[Iterable[TokenRecord]] = None,
    tokens: Optional[TokenManager] = None,
) -> bool:
    """True iff this `character_id` has more than one `tenant_tokens` row.

    Backend flag for the Characters UI (Phase 9 renders it). Not something
    the frontend infers by counting.
    """
    return len(_records_for(character_id, records=records, tokens=tokens)) > 1

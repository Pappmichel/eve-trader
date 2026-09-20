"""EVE SSO OAuth2 (Authorization Code + PKCE) handling.

At least one buyer (Jita) and one seller (the structure) character need to
authorize so that CHARACTER_ORDERS, WALLET_TRANSACTIONS and
STRUCTURE_MARKETS calls work for both roles - GitHub issue #46: more than
one of either is supported too (more registered sellers/buyers means more
available order slots), stored per-character as "buyer:<char_id>"/
"seller:<char_id>" via get_token_interactive_multi, same scheme already used
for "producer" characters - not a single fixed "buyer"/"seller" key anymore.
Tokens are cached per-character in the tenant_tokens Postgres table (see
storage.py) and refreshed automatically when expired - data/tokens.json was
the store before the multi-tenant migration's Phase 3b; import_tokens_file()
below is the one-time cutover helper that moved a real file's contents into
Postgres.

Usage:
    from eve_trader.auth import TokenManager
    tm = TokenManager()
    token = tm.get_token_interactive_multi("buyer", scopes)  # opens a browser once
    token = tm.get_token(token.role)                         # reuses/refreshes silently afterwards
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import re
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional

import requests

from . import storage
from .config import OAUTH_CONFIG, OAuthConfig


class InvalidRoleKey(ValueError):
    """role_key is outside the canonical `prefix:character_id` domain."""


# Canonical TokenManager / API role_key: one of the known prefixes, a colon,
# then a positive integer character id. Rejects path/SQL/unicode/null/blank
# by construction (F-05) — this is a domain, not just an injection denylist.
# `esi` is the Phase 4 grammar for a character-centric key; nothing writes
# one until Phase 6. Existing producer:/seller:/… keys stay valid.
_ROLE_KEY_RE = re.compile(
    r"^(buyer|seller|producer|doctrine|doctrine-assets|trader|esi):[1-9][0-9]{0,19}$"
)
ROLE_KEY_MAX_LENGTH = 48

# Authoritative tool ↔ role-prefix mapping (P5-02). Derived from the live
# TokenManager prefixes and api/routers/auth.py's login roles — not invented
# names. Ore & Minerals / refining / sorting / portfolio / admin have no
# ESI role_key namespace. "gate" is identity-only and is never a stored
# token key (excluded from _ROLE_KEY_RE).
TOOL_ROLE_PREFIXES: dict[str, frozenset[str]] = {
    "trading": frozenset({"buyer", "seller"}),
    "production": frozenset({"producer"}),
    "doctrine": frozenset({"doctrine", "doctrine-assets"}),
    "station_trading": frozenset({"trader"}),
}
ROLE_PREFIX_TOOL: dict[str, Optional[str]] = {
    prefix: tool
    for tool, prefixes in TOOL_ROLE_PREFIXES.items()
    for prefix in prefixes
}
ROLE_PREFIX_TOOL["gate"] = None


# P5-07 / P5-08: every prefix that ever had a legacy, pre-multi-character
# fixed-key TokenRecord (buyer/seller predate GitHub issue #46; doctrine/
# doctrine-assets/trader never did their own fixed-key era, listed here for
# completeness/defense-in-depth since they share this exact re-keying code
# path). "gate" is excluded - it is identity-only (access_gate.py) and never
# a TokenManager-stored key.
_LEGACY_BARE_ROLE_PREFIXES: tuple[str, ...] = tuple(p for p in ROLE_PREFIX_TOOL if p != "gate")


def _rekey_legacy_bare_roles(
    tokens: dict[str, "TokenRecord"],
) -> tuple[dict[str, "TokenRecord"], list[tuple[str, Optional[str]]]]:
    """Re-keys any bare "buyer"/"seller"/"producer"/... TokenRecord (the
    pre-multi-character storage key - see this module's own docstring) to
    "prefix:character_id". The record's own character_id is already known
    (resolved via ESI /oauth/verify at save time, see _to_record), so this
    never needs a live lookup - purely a local dict rewrite.

    Generalizes the one-time "producer" -> "producer:<id>" re-key
    import_tokens_file already did (a single fixed "producer" key predates
    multi-character tracking) to every role prefix, and to every load, not
    just the one-time file-import cutover. The pre-existing design ("an old
    token just keeps working under its original key until the user
    removes/re-adds it", see _list_role_characters's own docstring) is what
    a full-codebase security review confirmed broken two different ways:

    - P5-07: validate_role_key_for_tool's canonical-only grammar (F-05/
      P5-02) means a bare-keyed token can never be *removed* via the API
      once added - "removes/re-adds it" stopped being possible the moment
      that validation shipped, leaving the token stuck.
    - P5-08: esi_client's per-auth_role caches (F-02, GitHub issue #103)
      treat a bare "seller" as one principal, but it is only unique WITHIN
      a tenant - two different characters (in the same or different
      tenants) can each hold a legacy bare "seller" token and collide on
      the same class-level cache key. Canonical prefix:character_id keys
      are globally unique (EVE character IDs are never reused), closing
      both at the source - every caller that resolves an `auth_role` for
      an ESI call already does so via list_roles/get_record (see
      actions._list_role_characters, production.esi_sync.
      list_producer_characters), so migrating here reaches every one of
      them without a separate cache-side fix.

    Returns (possibly-rewritten tokens dict, list of (old_role, new_role)
    changes - new_role is None when the bare row was a stale duplicate of
    an already-canonical entry for the same character_id and was simply
    dropped, mirroring import_tokens_file's own "if new_role not in
    tokens" guard, rather than silently overwriting a live canonical
    record). Callers that persist tokens (TokenManager._load,
    import_tokens_file) use the returned changes to keep storage and the
    in-memory dict from disagreeing about which key is authoritative."""
    migrated = dict(tokens)
    changes: list[tuple[str, Optional[str]]] = []
    for role in list(migrated):
        if role not in _LEGACY_BARE_ROLE_PREFIXES:
            continue
        record = migrated.pop(role)
        new_role = f"{role}:{record.character_id}"
        if new_role in migrated:
            changes.append((role, None))
        else:
            migrated[new_role] = replace(record, role=new_role)
            changes.append((role, new_role))
    return migrated, changes


def validate_role_key(role_key: str) -> str:
    """Returns `role_key` if it is a canonical stored-token key, else raises
    InvalidRoleKey. Used on every HTTP-facing role_key argument."""
    if not isinstance(role_key, str):
        raise InvalidRoleKey("Invalid role_key.")
    if len(role_key) > ROLE_KEY_MAX_LENGTH:
        raise InvalidRoleKey("Invalid role_key.")
    if "\x00" in role_key or role_key != role_key.strip():
        raise InvalidRoleKey("Invalid role_key.")
    try:
        role_key.encode("ascii")
    except UnicodeEncodeError as e:
        raise InvalidRoleKey("Invalid role_key.") from e
    if _ROLE_KEY_RE.fullmatch(role_key) is None:
        raise InvalidRoleKey("Invalid role_key.")
    return role_key


def validate_role_key_for_tool(role_key: str, tool_key: str) -> str:
    """P5-02: syntactic validate_role_key plus tool-namespace ownership.

    A trading caller may only manipulate buyer:/seller: keys; a production
    caller only producer:; etc. Unknown tool_key or a prefix that belongs
    to a different tool raises InvalidRoleKey — same generic message as
    syntax failures, so the HTTP surface does not advertise the allowlist.
    """
    role_key = validate_role_key(role_key)
    prefix = role_key.split(":", 1)[0]
    allowed = TOOL_ROLE_PREFIXES.get(tool_key)
    if allowed is None or prefix not in allowed:
        raise InvalidRoleKey("Invalid role_key.")
    return role_key


@dataclass
class TokenRecord:
    role: str                 # arbitrary label, e.g. "buyer" / "seller"
    character_id: int
    character_name: str
    access_token: str
    refresh_token: str
    expires_at: float          # unix timestamp
    scopes: str

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return time.time() >= (self.expires_at - skew_seconds)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the ?code=...&state=... redirect from EVE SSO."""

    result: dict = {}

    def do_GET(self):  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.result["code"] = qs.get("code", [None])[0]
        _CallbackHandler.result["state"] = qs.get("state", [None])[0]
        _CallbackHandler.result["error"] = qs.get("error_description", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "Login successful, you can close this window." if _CallbackHandler.result["code"] \
            else f"Login failed: {_CallbackHandler.result['error']}"
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode("utf-8"))

    def log_message(self, format, *args):  # noqa: A002 - silence default logging
        pass


def _make_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


class TokenManager:
    # Class-level (not per-instance): every request/action creates its own
    # fresh TokenManager() that lazily re-reads the same tenant_tokens rows
    # from scratch (see _load), so a per-instance lock wouldn't stop two
    # concurrent requests - hitting FastAPI's sync-route thread pool at the
    # same time - from both seeing the same near-expiry token, both POSTing
    # a refresh, and racing to overwrite each other's saved record. Guards
    # get_token's check-expired -> refresh -> save sequence.
    _refresh_lock = threading.Lock()

    def __init__(self, cfg: OAuthConfig = OAUTH_CONFIG):
        self.cfg = cfg
        self._tokens: dict[str, TokenRecord] = {}
        self._loaded = False

    # ---------------------------------------------------------------- storage
    def _ensure_loaded(self) -> None:
        """Lazy - construction itself no longer touches storage (matches this
        class's own existing docstring: every request/action creates its own
        fresh TokenManager(), so deferring the read to first actual use costs
        nothing and lets a TokenManager be constructed even with no ambient
        tenant set yet, e.g. before the OAuth callback has resolved one -
        see api/routers/auth.py's callback())."""
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        """Force-reload from storage, bypassing the _loaded guard - used both
        by _ensure_loaded (first access) and by get_token's lock-protected
        re-check (a concurrent request may have already refreshed this exact
        role while we were waiting, so that check needs a genuinely fresh
        read, not the cached one).

        P5-07 / P5-08: also self-heals any bare legacy role key still
        sitting in storage (see _rekey_legacy_bare_roles's own docstring) -
        every request builds a fresh TokenManager (this class's own
        docstring), so the migration must be *persisted*, not just applied
        to this instance's in-memory dict, or a later remove_token(new_role)
        call on a different instance would target a storage row that still
        doesn't exist under that key."""
        self._tokens = {role: TokenRecord(**rec) for role, rec in storage.load_all_tenant_tokens().items()}
        self._tokens, changes = _rekey_legacy_bare_roles(self._tokens)
        if changes:
            with storage.batch_session():
                for old_role, new_role in changes:
                    if new_role is not None:
                        storage.save_tenant_token(new_role, asdict(self._tokens[new_role]))
                    storage.delete_tenant_token(old_role)
        self._loaded = True

    def _save_record(self, role: str) -> None:
        storage.save_tenant_token(role, asdict(self._tokens[role]))

    # ------------------------------------------------------------- SSO flow
    def get_token_interactive(self, role: str, scopes: Optional[list[str]] = None) -> TokenRecord:
        """Runs the full authorization-code + PKCE flow in a browser for `role`."""
        scopes = scopes or list(self.cfg.scopes)
        token_json = self._authorize_browser_flow(role, scopes)
        record = self._to_record(role, token_json, " ".join(scopes))
        self._tokens[role] = record
        self._save_record(role)
        return record

    def get_token_interactive_multi(self, role_prefix: str, scopes: list[str]) -> TokenRecord:
        """Same browser flow as get_token_interactive, but stores the result under
        f"{role_prefix}:{character_id}" (resolved *after* login, since which
        character logs in isn't known beforehand) instead of a fixed role name -
        lets multiple characters each hold their own token under one role family
        (e.g. several "producer" alts for ESI asset/industry tracking) without
        one login overwriting another's stored token."""
        token_json = self._authorize_browser_flow(role_prefix, scopes)
        character_id, character_name = self._verify(token_json["access_token"])
        final_role = f"{role_prefix}:{character_id}"
        record = self._to_record(final_role, token_json, " ".join(scopes),
                                  character_id=character_id, character_name=character_name)
        self._tokens[final_role] = record
        self._save_record(final_role)
        return record

    def _authorize_browser_flow(self, label: str, scopes: list[str]) -> dict:
        """Runs the authorization-code + PKCE dance and returns the raw token
        response. `label` is only used for the console log line."""
        if not self.cfg.client_id:
            raise RuntimeError(
                "EVE_SSO_CLIENT_ID is not set. Register an app at "
                "https://developers.eveonline.com and put the client id/secret "
                "in a .env file (see .env.example)."
            )
        state = secrets.token_urlsafe(16)
        verifier, challenge = _make_pkce_pair()

        params = {
            "response_type": "code",
            "redirect_uri": self.cfg.redirect_uri,
            "client_id": self.cfg.client_id,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{self.cfg.authorize_url}?{urllib.parse.urlencode(params)}"

        _CallbackHandler.result = {}
        try:
            server = http.server.HTTPServer((self.cfg.callback_host, self.cfg.callback_port), _CallbackHandler)
        except OSError as e:
            # Confirmed real gap: this CLI flow binds its own throwaway
            # HTTPServer on the exact same host:port as the running FastAPI
            # backend's /api/auth/callback (same EVE_SSO_CALLBACK_HOST/PORT -
            # the redirect_uri registered with EVE SSO is one fixed URL, so
            # they can't both listen at once). Used to surface as a raw
            # "Address already in use" OSError traceback with no indication
            # of *why* or what to do about it.
            raise RuntimeError(
                f"Can't start the CLI login server on {self.cfg.callback_host}:"
                f"{self.cfg.callback_port} ({e}) - something else (usually the "
                "running backend, e.g. `uvicorn eve_trader.api.main:app`) is "
                "already using that port. Stop it first, or log in via the web "
                "app instead (it has its own /api/auth/callback route and "
                "doesn't need this CLI flow at all)."
            ) from e
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        print(f"[{label}] Opening browser for EVE SSO login: {url}")
        webbrowser.open(url)
        thread.join(timeout=300)
        server.server_close()

        result = _CallbackHandler.result
        if not result.get("code"):
            raise RuntimeError(f"SSO login failed or timed out: {result.get('error')}")
        if result.get("state") != state:
            raise RuntimeError("SSO state mismatch - possible CSRF, aborting.")

        return self._exchange_code(result["code"], verifier)

    def _exchange_code(self, code: str, verifier: str) -> dict:
        resp = requests.post(
            self.cfg.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.cfg.client_id,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _refresh(self, record: TokenRecord) -> TokenRecord:
        resp = requests.post(
            self.cfg.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": record.refresh_token,
                "client_id": self.cfg.client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        token_json = resp.json()
        new_record = self._to_record(record.role, token_json, record.scopes,
                                      character_id=record.character_id,
                                      character_name=record.character_name)
        self._tokens[record.role] = new_record
        self._save_record(record.role)
        return new_record

    def _to_record(self, role: str, token_json: dict, scopes: str,
                    character_id: Optional[int] = None,
                    character_name: Optional[str] = None) -> TokenRecord:
        access_token = token_json["access_token"]
        refresh_token = token_json.get("refresh_token", "")
        expires_at = time.time() + token_json.get("expires_in", 1200)
        if character_id is None or character_name is None:
            character_id, character_name = self._verify(access_token)
        return TokenRecord(
            role=role, character_id=character_id, character_name=character_name,
            access_token=access_token, refresh_token=refresh_token,
            expires_at=expires_at, scopes=scopes,
        )

    @staticmethod
    def _verify(access_token: str) -> tuple[int, str]:
        """Resolves character id/name from an access token via /oauth/verify."""
        resp = requests.get(
            "https://login.eveonline.com/oauth/verify",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return int(data["CharacterID"]), data["CharacterName"]

    # ------------------------------------------------------------- public API
    def get_token(self, role: str) -> TokenRecord:
        self._ensure_loaded()
        record = self._tokens.get(role)
        if record is None:
            raise RuntimeError(
                f"No stored token for role '{role}'. Run get_token_interactive() first, "
                f"e.g.: `eve-trader auth --role {role}`."
            )
        if record.is_expired():
            with self._refresh_lock:
                # Re-load + re-check after acquiring the lock - a concurrent
                # request may have already refreshed (and saved) this exact
                # role while we were waiting, in which case just use that
                # instead of refreshing again and racing to overwrite it.
                self._load()
                record = self._tokens.get(role, record)
                if record.is_expired():
                    record = self._refresh(record)
        return record

    def has_token(self, role: str) -> bool:
        self._ensure_loaded()
        return role in self._tokens

    def get_record(self, role: str) -> Optional[TokenRecord]:
        """Like get_token, but never refreshes - character_id/character_name
        are plain fields on the stored record, not derived from a live
        access token, so listing who's registered shouldn't risk a refresh
        failure (e.g. a revoked/stale refresh token) any more than reading a
        dict. Used by list_producer_characters, which is on the hot path of
        every 'characters' sidebar render and every sync_esi() call - a
        single dead token there used to take the whole list down via
        get_token's raise, instead of just that one character."""
        self._ensure_loaded()
        return self._tokens.get(role)

    def auth_header(self, role: str) -> dict:
        return {"Authorization": f"Bearer {self.get_token(role).access_token}"}

    def list_roles(self, prefix: str) -> list[str]:
        """Returns all stored role keys starting with f"{prefix}:" (see
        get_token_interactive_multi), e.g. every registered "producer:<id>"."""
        self._ensure_loaded()
        return [role for role in self._tokens if role.startswith(f"{prefix}:")]

    def list_records(self) -> list[TokenRecord]:
        """Every stored token for the current tenant, regardless of prefix.

        The Phase 4 selector uses this instead of walking `list_roles` per
        prefix. Prefix keys stay in the pool (decision 2); this listing
        does not rewrite them.
        """
        self._ensure_loaded()
        return list(self._tokens.values())

    def remove_token(self, role: str) -> None:
        """Unconditional delete (idempotent at the DB layer - a role with no
        stored row is simply a no-op), not "check then delete" - no longer
        needs a full load first the way the old whole-file rewrite did.

        P5-07: deliberately does NOT call _ensure_loaded()/trigger
        _rekey_legacy_bare_roles itself - every real (HTTP-validated) caller
        already passes a canonical "prefix:character_id" role
        (validate_role_key_for_tool rejects anything else before this is
        ever reached), and the only way a caller can *know* that exact
        character_id is from an earlier list call in the same tenant/
        session, which has already loaded (and so already migrated/
        persisted) this tenant's tokens - see _load()'s own docstring.
        Self-loading here would be actively wrong for the one case where
        `role` itself is still a bare legacy key: _load() would rename it
        (e.g. "seller" -> "seller:42") *before* the pop/delete below ever
        runs, so the delete would target the now-nonexistent old key and
        silently fail to remove the (renamed, still-live) row - confirmed
        by test_remove_token_is_idempotent_even_with_no_stored_row, which
        exercises exactly this bare-key call shape directly."""
        self._tokens.pop(role, None)
        storage.delete_tenant_token(role)


def import_tokens_file(tenant_id: str, path: Optional[Path] = None) -> int:
    """One-time cutover helper (multi-tenant migration Phase 3b): reads a
    file-based tokens.json (the format TokenManager itself wrote before its
    Postgres cutover) and upserts every record into tenant_tokens for
    `tenant_id`. Shares _rekey_legacy_bare_roles with TokenManager._load()
    (P5-07 / P5-08) - every bare legacy role key in the file (not just a
    fixed "producer", the original one-time re-key this used to do alone)
    is normalized to "prefix:<character_id>" before it ever reaches
    Postgres, so a Postgres-backed TokenManager never needs to understand
    that legacy on-disk shape again once this has run. Idempotent - every
    write is an upsert, so re-running this against the same file just
    overwrites with the same data. Returns the number of records
    imported."""
    path = path or OAUTH_CONFIG.token_store_path
    raw = json.loads(path.read_text(encoding="utf-8"))
    tokens = {role: TokenRecord(**rec) for role, rec in raw.items()}
    tokens, _changes = _rekey_legacy_bare_roles(tokens)
    with storage.tenant_context(tenant_id):
        for role, record in tokens.items():
            storage.save_tenant_token(role, asdict(record))
    return len(tokens)

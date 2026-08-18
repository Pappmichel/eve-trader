"""EVE SSO OAuth2 (Authorization Code + PKCE) handling.

Two characters need to authorize (buyer in Jita, seller in the structure) so
that CHARACTER_ORDERS, WALLET_TRANSACTIONS and STRUCTURE_MARKETS calls work
for both. Tokens are cached per-character in the tenant_tokens Postgres
table (see storage.py) and refreshed automatically when expired -
data/tokens.json was the store before the multi-tenant migration's Phase 3b;
import_tokens_file() below is the one-time cutover helper that moved a real
file's contents into Postgres.

Usage:
    from eve_trader.auth import TokenManager
    tm = TokenManager()
    token = tm.get_token_interactive("buyer")   # opens a browser once
    token = tm.get_token("buyer")               # reuses/refreshes silently afterwards
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import requests

from . import storage
from .config import OAUTH_CONFIG, OAuthConfig


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
        read, not the cached one)."""
        self._tokens = {role: TokenRecord(**rec) for role, rec in storage.load_all_tenant_tokens().items()}
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

    def remove_token(self, role: str) -> None:
        """Unconditional delete (idempotent at the DB layer - a role with no
        stored row is simply a no-op), not "check then delete" - no longer
        needs a full load first the way the old whole-file rewrite did."""
        self._tokens.pop(role, None)
        storage.delete_tenant_token(role)


def import_tokens_file(tenant_id: str, path: Optional[Path] = None) -> int:
    """One-time cutover helper (multi-tenant migration Phase 3b): reads a
    file-based tokens.json (the format TokenManager itself wrote before its
    Postgres cutover) and upserts every record into tenant_tokens for
    `tenant_id`. Applies the same one-time "producer" -> "producer:<id>"
    re-keying TokenManager's old _load() used to (a single fixed "producer"
    role predates multi-character tracking) - a Postgres-backed
    TokenManager never needs to understand that legacy on-disk shape again
    once this has run. Idempotent - every write is an upsert, so re-running
    this against the same file just overwrites with the same data. Returns
    the number of records imported."""
    path = path or OAUTH_CONFIG.token_store_path
    raw = json.loads(path.read_text(encoding="utf-8"))
    tokens = {role: TokenRecord(**rec) for role, rec in raw.items()}
    old_producer = tokens.pop("producer", None)
    if old_producer is not None:
        new_role = f"producer:{old_producer.character_id}"
        if new_role not in tokens:
            old_producer.role = new_role
            tokens[new_role] = old_producer
    with storage.tenant_context(tenant_id):
        for role, record in tokens.items():
            storage.save_tenant_token(role, asdict(record))
    return len(tokens)

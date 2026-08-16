"""EVE SSO OAuth2 (Authorization Code + PKCE) handling.

Two characters need to authorize (buyer in Jita, seller in the structure) so
that CHARACTER_ORDERS, WALLET_TRANSACTIONS and STRUCTURE_MARKETS calls work
for both. Tokens are cached per-character in data/tokens.json and refreshed
automatically when expired.

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
    # fresh TokenManager() that re-reads the same tokens.json from scratch
    # (see _load), so a per-instance lock wouldn't stop two concurrent
    # requests - hitting FastAPI's sync-route thread pool at the same time -
    # from both seeing the same near-expiry token, both POSTing a refresh,
    # and racing to overwrite tokens.json last. Guards get_token's
    # check-expired -> refresh -> save sequence.
    _refresh_lock = threading.Lock()

    def __init__(self, cfg: OAuthConfig = OAUTH_CONFIG):
        self.cfg = cfg
        self._tokens: dict[str, TokenRecord] = {}
        self._load()

    # ---------------------------------------------------------------- storage
    def _load(self) -> None:
        path = self.cfg.token_store_path
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._tokens = {role: TokenRecord(**rec) for role, rec in raw.items()}
            self._migrate_single_producer_role()

    def _migrate_single_producer_role(self) -> None:
        """One-time migration: the first version of multi-character ESI tracking
        stored a single fixed "producer" role. Re-key it to the
        get_token_interactive_multi format ("producer:<character_id>") so it
        shows up alongside any additional characters added afterwards, instead
        of silently vanishing."""
        old = self._tokens.pop("producer", None)
        if old is None:
            return
        new_role = f"producer:{old.character_id}"
        if new_role not in self._tokens:
            old.role = new_role
            self._tokens[new_role] = old
        self._save()

    def _save(self) -> None:
        path = self.cfg.token_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({role: asdict(rec) for role, rec in self._tokens.items()}, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------- SSO flow
    def get_token_interactive(self, role: str, scopes: Optional[list[str]] = None) -> TokenRecord:
        """Runs the full authorization-code + PKCE flow in a browser for `role`."""
        scopes = scopes or list(self.cfg.scopes)
        token_json = self._authorize_browser_flow(role, scopes)
        record = self._to_record(role, token_json, " ".join(scopes))
        self._tokens[role] = record
        self._save()
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
        self._save()
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
        server = http.server.HTTPServer((self.cfg.callback_host, self.cfg.callback_port), _CallbackHandler)
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
        self._save()
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
        return self._tokens.get(role)

    def auth_header(self, role: str) -> dict:
        return {"Authorization": f"Bearer {self.get_token(role).access_token}"}

    def list_roles(self, prefix: str) -> list[str]:
        """Returns all stored role keys starting with f"{prefix}:" (see
        get_token_interactive_multi), e.g. every registered "producer:<id>"."""
        return [role for role in self._tokens if role.startswith(f"{prefix}:")]

    def remove_token(self, role: str) -> None:
        if role in self._tokens:
            del self._tokens[role]
            self._save()

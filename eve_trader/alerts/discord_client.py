"""Minimal Discord client: OAuth2 account linking (`identify` scope) and bot
DMs. Only ever talks to the fixed Discord API host. Never logs message bodies,
tokens or OAuth codes, and every message goes out with mentions disabled so a
mail subject or skill name can never ping @everyone."""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlencode

import requests

from .config import DiscordConfig, load_config

log = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
MAX_CONTENT_CHARS = 2000
TIMEOUT_SECONDS = 10
_USER_ID_RE = re.compile(r"^\d{15,25}$")


class DiscordError(Exception):
    """A Discord call failed. The message is safe to show to the user."""


class DiscordRateLimited(DiscordError):
    def __init__(self, retry_after: float):
        super().__init__("Discord is rate limiting this bot; try again later.")
        self.retry_after = retry_after


class DiscordDMBlocked(DiscordError):
    """The user does not accept DMs from this bot (no shared server, or DMs off)."""


def valid_user_id(value: object) -> bool:
    return isinstance(value, str) and bool(_USER_ID_RE.match(value))


def authorize_url(state: str, cfg: Optional[DiscordConfig] = None) -> str:
    cfg = cfg or load_config()
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": cfg.client_id, "response_type": "code", "scope": "identify",
        "redirect_uri": cfg.redirect_uri, "state": state, "prompt": "none",
    })


def _check(resp: requests.Response, what: str) -> requests.Response:
    if resp.status_code == 429:
        try:
            retry = float(resp.json().get("retry_after", 5))
        except (ValueError, AttributeError):
            retry = 5.0
        raise DiscordRateLimited(retry)
    if resp.status_code >= 400:
        # Status only - the body can echo request data.
        log.warning("Discord %s failed: HTTP %s", what, resp.status_code)
        if resp.status_code == 403 and what == "send message":
            raise DiscordDMBlocked("Discord refused the DM (the account does not accept DMs from this bot).")
        raise DiscordError(f"Discord {what} failed (HTTP {resp.status_code}).")
    return resp


def exchange_code_for_user_id(code: str, cfg: Optional[DiscordConfig] = None) -> str:
    """OAuth2 code -> the Discord user id. The access token is used once for
    `/users/@me` and dropped; nothing is stored."""
    cfg = cfg or load_config()
    if not cfg.can_link:
        raise DiscordError("Discord linking is not configured on this server.")
    try:
        token = _check(requests.post(
            f"{API_BASE}/oauth2/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": cfg.redirect_uri},
            auth=(cfg.client_id, cfg.client_secret), timeout=TIMEOUT_SECONDS,
        ), "token exchange").json().get("access_token")
        if not token:
            raise DiscordError("Discord returned no access token.")
        user_id = str(_check(requests.get(
            f"{API_BASE}/users/@me", headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_SECONDS,
        ), "user lookup").json().get("id", ""))
    except requests.RequestException as e:
        raise DiscordError("Could not reach Discord.") from e
    if not valid_user_id(user_id):
        raise DiscordError("Discord returned an invalid user id.")
    return user_id


def send_dm(user_id: str, content: str, cfg: Optional[DiscordConfig] = None) -> None:
    cfg = cfg or load_config()
    if not cfg.can_send:
        raise DiscordError("The Discord bot is not configured on this server.")
    if not valid_user_id(user_id):
        raise DiscordError("Invalid Discord user id.")
    headers = {"Authorization": f"Bot {cfg.bot_token}"}
    try:
        channel = _check(requests.post(
            f"{API_BASE}/users/@me/channels", json={"recipient_id": user_id},
            headers=headers, timeout=TIMEOUT_SECONDS,
        ), "open DM").json().get("id")
        if not channel:
            raise DiscordError("Discord returned no DM channel.")
        _check(requests.post(
            f"{API_BASE}/channels/{channel}/messages",
            json={"content": content[:MAX_CONTENT_CHARS], "allowed_mentions": {"parse": []}},
            headers=headers, timeout=TIMEOUT_SECONDS,
        ), "send message")
    except requests.RequestException as e:
        raise DiscordError("Could not reach Discord.") from e

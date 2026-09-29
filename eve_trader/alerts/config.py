"""Operator-level Discord settings, read from the environment on every call
(secrets, not config.yaml values - same reasoning as `OAuthConfig`). The bot
token is never stored per tenant and never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..config import OAUTH_CONFIG


@dataclass(frozen=True)
class DiscordConfig:
    bot_token: str
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def can_send(self) -> bool:
        return bool(self.bot_token)

    @property
    def can_link(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


def load_config() -> DiscordConfig:
    default_redirect = OAUTH_CONFIG.frontend_origin.rstrip("/") + "/api/char-alerts/discord/callback"
    return DiscordConfig(
        bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
        client_id=os.getenv("DISCORD_CLIENT_ID", ""),
        client_secret=os.getenv("DISCORD_CLIENT_SECRET", ""),
        redirect_uri=os.getenv("DISCORD_REDIRECT_URI", default_redirect),
    )

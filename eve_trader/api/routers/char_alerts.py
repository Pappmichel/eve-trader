"""Discord alerts routes - thin wrappers around alerts/actions.py. Gated on
tool_key `char_alerts` via `_TOOL_PATH_PREFIXES` (api/app.py). The Discord
OAuth callback is a browser redirect, so it answers with a redirect back to the
frontend instead of JSON."""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ...actions import ActionError
from ...alerts import actions as alert_actions
from ...config import OAUTH_CONFIG

router = APIRouter()

_UI_PATH = "/character-management/alerts"


class SubscriptionRequest(BaseModel):
    character_id: int
    alert_type: str
    enabled: bool
    include_content: bool = False
    lead_hours: Optional[int] = None


def _wrap(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/settings")
def settings():
    return _wrap(alert_actions.do_get_alert_settings)


@router.post("/subscriptions")
def set_subscription(body: SubscriptionRequest):
    return _wrap(
        alert_actions.do_set_subscription, character_id=body.character_id, alert_type=body.alert_type,
        enabled=body.enabled, include_content=body.include_content, lead_hours=body.lead_hours,
    )


@router.get("/discord/start")
def discord_start():
    return _wrap(alert_actions.do_start_discord_link)


@router.get("/discord/callback")
def discord_callback(code: str = "", state: str = "", error: str = ""):
    outcome = "linked"
    if error or not code:
        outcome = "cancelled"
    else:
        try:
            alert_actions.do_finish_discord_link(code=code, state=state)
        except ActionError:
            outcome = "error"
    return RedirectResponse(
        OAUTH_CONFIG.frontend_origin.rstrip("/") + _UI_PATH + "?" + urlencode({"discord": outcome}),
    )


@router.delete("/discord")
def discord_unlink():
    return _wrap(alert_actions.do_unlink_discord)


@router.post("/test")
def test_message():
    return _wrap(alert_actions.do_send_test_message)

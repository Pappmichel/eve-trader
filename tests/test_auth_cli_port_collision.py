import http.server

import pytest

from eve_trader.auth import TokenManager
from eve_trader.config import OAuthConfig


def test_authorize_browser_flow_raises_clear_error_on_port_collision(monkeypatch):
    # Confirmed real gap: `eve-trader auth` binds its own throwaway
    # HTTPServer on the same callback_host:callback_port the running FastAPI
    # backend already listens on (both must match the one redirect_uri
    # registered with EVE SSO) - used to surface as a raw, unexplained
    # "Address already in use" OSError traceback.
    cfg = OAuthConfig(client_id="test-client-id")
    tm = TokenManager(cfg)

    def _raise_address_in_use(*args, **kwargs):
        raise OSError("[Errno 98] Address already in use")
    monkeypatch.setattr(http.server, "HTTPServer", _raise_address_in_use)

    with pytest.raises(RuntimeError, match="already using that port"):
        tm._authorize_browser_flow("buyer", list(cfg.scopes))

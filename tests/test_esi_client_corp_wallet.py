from eve_trader.config import OAUTH_CONFIG
from eve_trader.esi_client import ESIClient
from eve_trader.production.esi_sync import PRODUCTION_SCOPES


CORP_WALLET_SCOPE = "esi-wallet.read_corporation_wallets.v1"


def test_corp_wallet_scope_is_not_wired_until_portal_confirmation():
    """Requesting a scope the EVE developer portal has not enabled makes SSO
    reject the entire login with invalid_scope. Phase 8 implements the
    client methods but must not add this scope to any live scope list yet."""
    assert CORP_WALLET_SCOPE not in OAUTH_CONFIG.scopes
    assert CORP_WALLET_SCOPE not in PRODUCTION_SCOPES


def test_corporation_wallet_transactions_uses_from_id_not_pages(monkeypatch):
    captured = []

    def _fake_get(self, path, params=None, auth_role=None):
        captured.append((path, dict(params or {}), auth_role))
        return []

    monkeypatch.setattr(ESIClient, "_get", _fake_get)

    ESIClient().corporation_wallet_transactions(98_000_000, 3, "seller:1", from_id=99)

    assert len(captured) == 1
    path, params, role = captured[0]
    assert path == "/corporations/98000000/wallets/3/transactions/"
    assert params["datasource"] == "tranquility"
    assert params["from_id"] == 99
    assert "page" not in params
    assert role == "seller:1"


def test_corporation_wallet_transactions_omits_from_id_on_first_page(monkeypatch):
    captured = []

    def _fake_get(self, path, params=None, auth_role=None):
        captured.append(dict(params or {}))
        return []

    monkeypatch.setattr(ESIClient, "_get", _fake_get)

    ESIClient().corporation_wallet_transactions(1, 1, "buyer:1")

    assert "from_id" not in captured[0]


def test_corporation_wallet_journal_uses_get_all_pages(monkeypatch):
    captured = []

    def _fake_get_all_pages(self, path, params=None, auth_role=None, max_workers=6):
        captured.append((path, dict(params or {}), auth_role))
        return []

    monkeypatch.setattr(ESIClient, "_get_all_pages", _fake_get_all_pages)

    ESIClient().corporation_wallet_journal(98_000_000, 4, "seller:1")

    assert len(captured) == 1
    path, params, role = captured[0]
    assert path == "/corporations/98000000/wallets/4/journal/"
    assert params == {"datasource": "tranquility"}
    assert role == "seller:1"

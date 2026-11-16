"""Local verification of the SSO access token (JWKS, claims, scopes)."""
import json
import time

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

from eve_trader.auth import IdentityError, TokenManager
from eve_trader.config import OAuthConfig

CLIENT_ID = "test-client-id"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid):
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    data.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return data


def _token(private_key, kid="k1", **overrides):
    claims = {
        "iss": "https://login.eveonline.com", "aud": [CLIENT_ID, "EVE Online"],
        "sub": "CHARACTER:EVE:4242", "name": "Test Pilot", "owner": "hash-1",
        "scp": ["esi-assets.read_assets.v1"], "exp": int(time.time()) + 600,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


class _Jwks:
    def __init__(self, keys):
        self.keys = keys
        self.calls = 0
        self.fail = False

    def get(self, url, timeout=None, **kw):
        self.calls += 1
        if self.fail:
            raise requests.ConnectionError("down")
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps({"keys": self.keys}).encode()
        return resp


@pytest.fixture
def jwks(monkeypatch):
    TokenManager._jwks_keys = {}
    TokenManager._jwks_fetched_at = 0.0
    TokenManager._jwks_url = ""
    key = _key()
    server = _Jwks([_jwk(key, "k1")])
    server.private = key
    monkeypatch.setattr("eve_trader.auth.requests.get", server.get)
    yield server
    TokenManager._jwks_keys = {}
    TokenManager._jwks_fetched_at = 0.0
    TokenManager._jwks_url = ""


def _tm():
    return TokenManager(OAuthConfig(client_id=CLIENT_ID))


def test_valid_token_yields_identity(jwks):
    identity = _tm()._identity(_token(jwks.private))
    assert (identity.character_id, identity.character_name) == (4242, "Test Pilot")
    assert identity.owner_hash == "hash-1"
    assert identity.scopes == ("esi-assets.read_assets.v1",)
    assert _tm()._verify(_token(jwks.private)) == (4242, "Test Pilot")


@pytest.mark.parametrize("scp,expected", [
    ("a.v1 b.v1", ("a.v1", "b.v1")), (["a.v1"], ("a.v1",)), (None, ()),
])
def test_scp_string_list_or_missing(jwks, scp, expected):
    assert _tm()._identity(_token(jwks.private, scp=scp)).scopes == expected


def test_bare_issuer_is_accepted(jwks):
    assert _tm()._identity(_token(jwks.private, iss="login.eveonline.com")).character_id == 4242


@pytest.mark.parametrize("overrides", [
    {"aud": ["other", "EVE Online"]}, {"aud": [CLIENT_ID]},
    {"iss": "https://evil.example"}, {"exp": int(time.time()) - 600},
    {"sub": "CORPORATION:1"},
])
def test_bad_claims_are_rejected(jwks, overrides):
    with pytest.raises(IdentityError):
        _tm()._identity(_token(jwks.private, **overrides))


def test_wrong_signature_is_rejected(jwks):
    with pytest.raises(IdentityError):
        _tm()._identity(_token(_key()))


def test_unknown_kid_reloads_jwks_exactly_once(jwks):
    tm = _tm()
    tm._identity(_token(jwks.private))
    assert jwks.calls == 1
    with pytest.raises(IdentityError):
        tm._identity(_token(jwks.private, kid="rotated"))
    assert jwks.calls == 2
    new_key = _key()
    jwks.keys.append(_jwk(new_key, "rotated"))
    assert tm._identity(_token(new_key, kid="rotated")).character_id == 4242
    assert jwks.calls == 3
    tm._identity(_token(new_key, kid="rotated"))
    assert jwks.calls == 3


def test_jwks_outage_fails_identity_but_not_refresh(jwks, monkeypatch):
    jwks.fail = True
    with pytest.raises(IdentityError):
        _tm()._identity(_token(jwks.private))
    # A refresh builds the record from the stored identity: no key lookup.
    record = _tm()._to_record(
        "esi:4242", {"access_token": "opaque", "refresh_token": "r"}, "a.v1",
        character_id=4242, character_name="Test Pilot", owner_hash="hash-1",
    )
    assert record.owner_hash == "hash-1" and jwks.calls == 1


def test_hs256_token_is_rejected(jwks):
    forged = jwt.encode({"sub": "CHARACTER:EVE:1", "name": "x"}, "secret", algorithm="HS256",
                        headers={"kid": "k1"})
    with pytest.raises(IdentityError):
        _tm()._identity(forged)

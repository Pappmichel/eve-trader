"""ESIClient._write and the mail write calls (docs/CHARACTER_MANAGEMENT_PLAN.md
R3): a mail must never be sent twice by a retry, and ESI's 201/204 answers must
count as success. No network, no Postgres."""
from __future__ import annotations

import pytest
import requests

from eve_trader.esi_client import ESIClient, ESIDeliveryUnknown, ESIError, ESIHTTPError


class _Resp:
    def __init__(self, status, body="", json_value=None, headers=None):
        self.status_code, self.text, self._json = status, body, json_value
        self.headers = headers or {}

    def json(self):
        return self._json


class _Session:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple] = []

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        self.calls.append((method, url, json, params, headers))
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class _Tokens:
    def __init__(self, fail=False):
        self.fail = fail

    def auth_header(self, role):
        if self.fail:
            raise requests.ConnectionError("refresh endpoint down")
        return {"Authorization": f"Bearer token-for-{role}"}


def _client(script, tokens=None, monkeypatch=None):
    esi = ESIClient.__new__(ESIClient)
    esi.cfg = type("Cfg", (), {"esi_base": "https://esi.test"})()
    esi.tokens = tokens or _Tokens()
    esi.session = _Session(script)
    return esi


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("eve_trader.esi_client.time.sleep", lambda s: None)
    monkeypatch.setattr(ESIClient, "_await_error_budget", classmethod(lambda cls: None))


PAYLOAD = {"approved_cost": 0, "body": "b", "subject": "s",
           "recipients": [{"recipient_id": 5, "recipient_type": "character"}]}


def test_send_mail_accepts_201_and_returns_the_mail_id():
    esi = _client([_Resp(201, json_value=987)])
    assert esi.send_mail(1001, "esi:1001", PAYLOAD) == 987
    (method, url, body, params, headers), = esi.session.calls
    assert (method, url) == ("POST", "https://esi.test/characters/1001/mail/")
    assert body == PAYLOAD and params == {"datasource": "tranquility"}
    assert headers == {"Authorization": "Bearer token-for-esi:1001"}


@pytest.mark.parametrize("failure", [
    requests.ConnectionError("reset"), requests.Timeout("slow"), _Resp(500), _Resp(502), _Resp(503), _Resp(504),
])
def test_send_mail_is_never_retried_and_reports_an_unknown_delivery(failure):
    esi = _client([failure, _Resp(201, json_value=1)])       # a retry would "succeed" and double-send
    with pytest.raises(ESIDeliveryUnknown):
        esi.send_mail(1001, "esi:1001", PAYLOAD)
    assert len(esi.session.calls) == 1
    assert len(esi.session.script) == 1                       # the second answer was never asked for


def test_a_rate_limited_send_is_retried_because_esi_rejected_it_before_processing():
    esi = _client([_Resp(429, headers={"Retry-After": "1"}), _Resp(420), _Resp(201, json_value=42)])
    assert esi.send_mail(1001, "esi:1001", PAYLOAD) == 42
    assert len(esi.session.calls) == 3


def test_a_rate_limit_that_never_clears_ends_in_an_http_error_not_a_hang():
    esi = _client([_Resp(429)] * 3)
    with pytest.raises(ESIHTTPError) as exc:
        esi.send_mail(1001, "esi:1001", PAYLOAD)
    assert exc.value.status == 429 and len(esi.session.calls) == 3


def test_a_definite_refusal_carries_status_and_body_but_not_in_its_message():
    esi = _client([_Resp(400, body='{"error": "secret recipient list"}')])
    with pytest.raises(ESIHTTPError) as exc:
        esi.send_mail(1001, "esi:1001", PAYLOAD)
    assert exc.value.status == 400
    assert "secret" in exc.value.body                       # for server-side parsing only
    assert "secret" not in str(exc.value) and "HTTP 400" in str(exc.value)
    assert len(esi.session.calls) == 1


def test_update_mail_is_idempotent_and_retries_5xx_and_transport_errors():
    esi = _client([_Resp(503), requests.ConnectionError("blip"), _Resp(204)])
    esi.update_mail(1001, "esi:1001", 55, {"read": True})
    assert [c[0] for c in esi.session.calls] == ["PUT", "PUT", "PUT"]
    assert esi.session.calls[0][2] == {"read": True}
    assert esi.session.calls[0][1] == "https://esi.test/characters/1001/mail/55/"


def test_an_idempotent_write_that_keeps_failing_gives_up_with_a_plain_error():
    esi = _client([requests.ConnectionError("down")] * 3)
    with pytest.raises(ESIError) as exc:
        esi.update_mail(1001, "esi:1001", 55, {"labels": [32]})
    assert not isinstance(exc.value, ESIDeliveryUnknown)      # safe to retry manually, unlike a send
    assert len(esi.session.calls) == 3


def test_delete_calls_accept_204_and_report_404():
    esi = _client([_Resp(204), _Resp(204), _Resp(404, body="gone")])
    esi.delete_mail(1001, "r", 5)
    esi.delete_mail_label(1001, "r", 32)
    with pytest.raises(ESIHTTPError) as exc:
        esi.delete_mail(1001, "r", 6)
    assert exc.value.status == 404
    assert [(c[0], c[1]) for c in esi.session.calls[:2]] == [
        ("DELETE", "https://esi.test/characters/1001/mail/5/"),
        ("DELETE", "https://esi.test/characters/1001/mail/labels/32/"),
    ]


def test_create_label_posts_name_and_colour_and_is_not_retried_on_5xx():
    esi = _client([_Resp(201, json_value=77)])
    assert esi.create_mail_label(1001, "r", "Contracts", "#ff6600") == 77
    assert esi.session.calls[0][2] == {"name": "Contracts", "color": "#ff6600"}
    failing = _client([_Resp(502), _Resp(201, json_value=78)])
    with pytest.raises(ESIDeliveryUnknown):
        failing.create_mail_label(1001, "r", "Contracts", "#ff6600")     # a retry would create two labels
    assert len(failing.session.calls) == 1


def test_a_dead_refresh_token_fails_fast_and_never_reaches_esi():
    esi = _client([_Resp(201, json_value=1)], tokens=_Tokens(fail=True))
    with pytest.raises(ESIError, match="Token refresh failed"):
        esi.send_mail(1001, "esi:1001", PAYLOAD)
    assert esi.session.calls == []


def test_recipient_and_search_helpers(monkeypatch):
    esi = _client([])
    monkeypatch.setattr(esi, "_post_universe_ids", lambda names: {
        "characters": [{"id": 1, "name": "Alice"}], "corporations": [{"id": 2, "name": "Corp"}],
    })
    out = esi.resolve_recipient_names(["Alice", "Alice", "Corp", ""])
    assert out == {"character": [{"id": 1, "name": "Alice"}], "corporation": [{"id": 2, "name": "Corp"}], "alliance": []}

    monkeypatch.setattr(esi, "_get", lambda path, params=None, **kw: {"character": [10, 11, 12], "alliance": [30]})
    monkeypatch.setattr(esi, "resolve_names_cached", lambda ids: {i: f"N{i}" for i in ids if i != 11})
    hits = esi.search_entities("ali", limit=2)
    assert hits == [
        {"type": "character", "id": 10, "name": "N10"},       # 11 has no resolvable name -> dropped
        {"type": "alliance", "id": 30, "name": "N30"},        # limit=2 cut character 12
    ]

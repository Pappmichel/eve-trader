"""Public affiliation lookup used by the access gate. Not cached."""
from eve_trader.esi_client import ESIClient


def test_character_affiliation_parses_and_is_not_cached(monkeypatch):
    calls = []

    class _Resp:
        def json(self):
            return [
                {"character_id": 1, "corporation_id": 2, "alliance_id": 3},
                {"character_id": 4, "corporation_id": 5},
            ]

    def fake_post(self, path, body, params=None, retries=3, timeout=30):
        calls.append((path, list(body), timeout, retries))
        return _Resp()

    monkeypatch.setattr(ESIClient, "_post_response", fake_post)
    client = ESIClient()

    assert client.character_affiliation([1, 4, 1], timeout=5, retries=1) == {
        1: (2, 3), 4: (5, None),
    }
    assert client.character_affiliation([1], timeout=5, retries=1)[1] == (2, 3)
    assert len(calls) == 2
    assert calls[0][0] == "/characters/affiliation/"
    assert calls[0][1] == [1, 4]
    assert calls[0][2] == 5
    assert calls[0][3] == 1


def test_character_affiliation_chunks_at_1000(monkeypatch):
    sizes = []

    class _Resp:
        def json(self):
            return []

    def fake_post(self, path, body, params=None, retries=3, timeout=30):
        sizes.append(len(body))
        return _Resp()

    monkeypatch.setattr(ESIClient, "_post_response", fake_post)
    ESIClient().character_affiliation(list(range(1001)))
    assert sizes == [1000, 1]


def test_search_corporations_alliances_keeps_exact_matches(monkeypatch):
    monkeypatch.setattr(ESIClient, "_post_universe_ids", lambda self, names: {
        "corporations": [{"id": 10, "name": "Pandemic Horde"}],
        "alliances": [{"id": 20, "name": "Pandemic Horde"}, {"id": 21, "name": "Other"}],
    })
    hits = ESIClient().search_corporations_alliances("pandemic horde")
    assert hits == [
        {"type": "corporation", "id": 10, "name": "Pandemic Horde"},
        {"type": "alliance", "id": 20, "name": "Pandemic Horde"},
    ]

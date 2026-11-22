"""The bot's HTTP client for lol-amis — the one source of truth for players, teams and ratings."""

import aiohttp
import pytest

from bot.cogs.customs.lolamis import (
    UI_OPTS,
    LinkTaken,
    LolAmisClient,
    LolAmisError,
    Slot,
    Teams,
)


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self, content_type=None):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url, json))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


PERSON = {"id": 3, "name": "Adri", "rank_label": "Bronze I", "discord_id": "42", "archived": False}
GEN = {
    "blue": [{"person_id": i, "lane": i} for i in range(5)],
    "red": [{"person_id": 5 + i, "lane": i} for i in range(5)],
    "p_blue": 0.51,
}


def client(*responses):
    s = FakeSession(*responses)
    return LolAmisClient("http://lol-amis:8000/", s), s


async def test_list_persons_parses_and_hits_api():
    c, s = client(FakeResponse(200, [PERSON]))
    persons = await c.list_persons()
    assert s.calls == [("GET", "http://lol-amis:8000/api/persons", None)]
    assert persons[0].id == 3 and persons[0].discord_id == "42" and persons[0].rank_label == "Bronze I"


async def test_create_person_sends_name_and_discord_id():
    c, s = client(FakeResponse(200, PERSON))
    await c.create_person("Adri", "42")
    assert s.calls == [("POST", "http://lol-amis:8000/api/persons", {"name": "Adri", "discord_id": "42"})]


async def test_link_and_unlink():
    c, s = client(FakeResponse(200, PERSON), FakeResponse(200, {**PERSON, "discord_id": None}))
    await c.link(3, "42")
    await c.link(3, None)
    assert s.calls[0] == ("PATCH", "http://lol-amis:8000/api/persons/3", {"discord_id": "42"})
    assert s.calls[1][2] == {"discord_id": ""}


async def test_link_conflict_names_the_holder():
    c, _ = client(FakeResponse(409, {"detail": {"holder_id": 7, "holder_name": "Mehdi"}}))
    with pytest.raises(LinkTaken) as e:
        await c.link(3, "42")
    assert (e.value.holder_id, e.value.holder_name) == (7, "Mehdi")


async def test_generate_sends_the_ui_defaults_verbatim():
    assert UI_OPTS == {"lanes": "roles", "lane": True, "profile": True, "mix": True, "rand": 2, "duo": True}
    c, s = client(FakeResponse(200, GEN))
    teams = await c.generate(list(range(10)))
    assert s.calls == [("POST", "http://lol-amis:8000/api/generate", {"person_ids": list(range(10)), "opts": UI_OPTS})]
    assert teams.blue[0] == Slot(0, 0) and teams.red[4] == Slot(9, 4) and teams.p_blue == 0.51


async def test_evaluate_posts_current_teams():
    c, s = client(FakeResponse(200, GEN))
    t = Teams.from_json(GEN)
    await c.evaluate(t)
    method, url, body = s.calls[0]
    assert (method, url) == ("POST", "http://lol-amis:8000/api/generate/evaluate")
    assert body["blue"][0] == {"person_id": 0, "lane": 0} and body["opts"] == UI_OPTS


async def test_record_game_returns_id_and_marks_source():
    c, s = client(FakeResponse(200, {"id": 99}))
    gid = await c.record_game(Teams.from_json(GEN), "red")
    body = s.calls[0][2]
    assert gid == 99 and body["winner"] == "red" and body["source"] == "generateur"
    assert body["blue"][2] == {"person_id": 2, "lane": 2}


async def test_set_winner_puts_the_game():
    c, s = client(FakeResponse(200, {"id": 99}))
    await c.set_winner(99, Teams.from_json(GEN), "blue")
    assert s.calls[0][0:2] == ("PUT", "http://lol-amis:8000/api/games/99")
    assert s.calls[0][2]["winner"] == "blue"


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(500, {"detail": "boom"}),
        FakeResponse(200, ValueError("bad json")),
        aiohttp.ClientError(),
        TimeoutError(),
    ],
)
async def test_failures_become_lolamis_error(response):
    c, _ = client(response)
    with pytest.raises(LolAmisError):
        await c.list_persons()


def test_teams_json_round_trip():
    t = Teams.from_json(GEN)
    assert Teams.from_json(t.to_json()) == t


async def test_find_game_matches_exact_sides_and_skips_known_ids():
    other = {**GEN, "red": list(reversed(GEN["red"]))}  # same people, different lanes order -> same sets
    games = [
        {"id": 1, "source": "generateur", **GEN},
        {"id": 2, "source": "manuel", **GEN},
        {"id": 3, "source": "generateur", "blue": GEN["red"], "red": GEN["blue"]},
        {"id": 4, "source": "generateur", **other},
    ]
    c, _ = client(FakeResponse(200, games), FakeResponse(200, games))
    teams = Teams.from_json(GEN)
    assert await c.find_game(teams, exclude=set()) == 1
    assert await c.find_game(teams, exclude={1}) == 4

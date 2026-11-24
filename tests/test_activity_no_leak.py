# ruff: noqa: F811  (pytest fixtures are imported, then used as parameters)
import json

from bot.cogs.loldle import catalog
from tests.activity_game_fakes import auth, client, ticket, world  # noqa: F401
from tests.test_activity_game import guess, post_hint, three_wrong


def leaks(text: str, world) -> list[str]:
    a = world.answer
    needles = [a.id, a.name, "spells", "cdn", "ddragon", "communitydragon", *a.spells, a.splash]
    return [n for n in needles if n and n.lower() in text.lower()]


async def test_unsolved_state_never_contains_the_answer(client, world):
    await guess(client, world.wrong[0].name)
    await guess(client, world.wrong[1].name)
    r = await client.get("/api/state", headers=auth())
    assert leaks(await r.text(), world) == []
    assert (await r.json())["solved"] is None


async def test_every_unsolved_response_is_clean(client, world):
    """Guess, hint and unknown replies carry a state too: same guard."""
    await three_wrong(client, world)
    for resp in (
        await guess(client, "Pas un champion"),
        await guess(client, world.wrong[3].name),
        await post_hint(client, "spell"),
        await post_hint(client, "spell"),
    ):
        assert leaks(await resp.text(), world) == []


async def test_champion_list_has_only_id_and_name_and_does_not_mark_the_answer(client, world):
    body = await (await client.get("/api/champions", headers=auth())).json()
    assert len(body) == len(catalog.load())
    assert all(set(e) == {"id", "name"} for e in body)
    names = [e["name"] for e in body]
    assert names == sorted(names, key=catalog.normalise)
    world.answer = world.wrong[5]  # another answer for the day changes nothing about the list
    assert await (await client.get("/api/champions", headers=auth())).json() == body


async def test_image_hint_urls_carry_no_champion(client, world):
    await three_wrong(client, world)
    body = await (await post_hint(client, "spell")).json()
    assert leaks(json.dumps(body), world) == []
    r = await client.get("/api/hints/spell.png", params={"ticket": ticket("hint:spell")})
    assert leaks(str(r.url).split("?")[0] + "".join(f"{k}{v}" for k, v in r.headers.items()), world) == []


async def test_owned_hints_never_name_the_answer(client, world):
    await three_wrong(client, world)
    for kind in ("spell", "splash", "letter"):
        await post_hint(client, kind)
    state = await (await client.get("/api/state", headers=auth())).json()
    assert world.answer.name not in json.dumps(state)

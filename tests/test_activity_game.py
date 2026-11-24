# ruff: noqa: F811  (pytest fixtures are imported, then used as parameters)
import asyncio
import io
import json

import pytest
from PIL import Image

from activity.server import assets, game
from activity.server.keys import HINT_CACHE, HTTP, ICON_CACHE
from bot.cogs.loldle import catalog, hints, rules
from tests.activity_game_fakes import OTHER, PNG, USER, auth, client, make_client, ticket, world  # noqa: F401


async def guess(client, name, user=USER, **extra):
    client.world.now += 1  # one write a second: the limiter is tested on its own
    return await client.post("/api/guess", json={"champion": name, **extra}, headers=auth(user))


async def three_wrong(client, world):
    for c in world.wrong[:3]:
        assert (await guess(client, c.name)).status == 200


async def post_hint(client, kind, user=USER):
    client.world.now += 1
    return await client.post(f"/api/hints/{kind}", headers=auth(user))


async def test_state_requires_a_session(client):
    assert (await client.get("/api/state")).status == 401
    assert (await client.get("/api/champions")).status == 401
    assert (await client.post("/api/guess", json={"champion": "x"})).status == 401


async def test_state_of_a_fresh_member(client, world):
    body = await (await client.get("/api/state", headers=auth())).json()
    assert body["guesses"] == [] and body["solved"] is None and body["balance"] == 1000
    assert body["day"] == world.day.isoformat()
    assert body["hints"]["spell"] == {"owned": False, "price": 25, "available": False}
    assert body["live"] == []


async def test_guess_uses_server_day(client, world):
    body = await (await guess(client, world.wrong[0].name, day="1999-01-01")).json()
    assert body["status"] == "ok" and body["state"]["day"] == world.day.isoformat()
    assert len(world.guesses[USER]) == 1


async def test_guess_by_id_and_duplicate(client, world):
    c = world.wrong[0]
    assert (await (await guess(client, c.id)).json())["status"] == "ok"
    assert (await (await guess(client, c.name)).json())["status"] == "duplicate"
    assert len(world.guesses[USER]) == 1


async def test_unknown_champion_is_refused(client, world):
    body = await (await guess(client, "Pas un champion")).json()
    assert body["status"] == "unknown" and body["state"]["guesses"] == []
    assert world.guesses == {}


@pytest.mark.parametrize("payload", [{}, {"champion": ""}, {"champion": 5}, {"champion": "x" * 65}, [1]])
async def test_bad_guess_bodies_are_400(client, payload):
    r = await client.post("/api/guess", json=payload, headers=auth())
    assert r.status == 400 and (await r.json()) == {"error": "bad_request"}


async def test_solved_already_and_win(client, world):
    body = await (await guess(client, world.answer.name)).json()
    assert body["state"]["solved"]["tries"] == 1
    again = await (await guess(client, world.wrong[0].name)).json()
    assert again["status"] == "solved_already"


async def test_answer_name_appears_after_win(client, world):
    await three_wrong(client, world)
    state = await (await client.get("/api/state", headers=auth())).json()
    assert state["solved"] is None and world.answer.name not in str(state)
    body = await (await guess(client, world.answer.name)).json()
    assert body["state"]["solved"]["name"] == world.answer.name
    assert body["state"]["solved"]["streak"] == 1
    assert all(not h["available"] for h in body["state"]["hints"].values())


async def test_unknown_hint_kind_is_404(client):
    assert (await post_hint(client, "nope")).status == 404


async def test_hint_locked_before_three_guesses(client):
    assert (await (await post_hint(client, "spell")).json())["status"] == "locked"


async def test_hint_purchase_and_ownership(client, world):
    await three_wrong(client, world)
    body = await (await post_hint(client, "spell")).json()
    assert body["status"] == "bought" and body["balance"] == 975
    assert body["state"]["hints"]["spell"]["owned"] and body["state"]["hints"]["spell"]["text"]
    assert body["state"]["hints"]["splash"]["available"] is True
    assert (await (await post_hint(client, "spell")).json())["status"] == "owned"
    assert (await (await post_hint(client, "letter")).json())["status"] == "locked"


async def test_hint_poor(client, world):
    await three_wrong(client, world)
    world.balance[USER] = 10
    body = await (await post_hint(client, "spell")).json()
    assert body["status"] == "poor" and body["balance"] == 10


async def test_hint_rendered_before_charge(client, world):
    await three_wrong(client, world)
    world.render_fail = True
    body = await (await post_hint(client, "spell")).json()
    assert body["status"] == "unavailable" and body["balance"] == 1000
    assert world.buy_calls == [] and world.render_calls == ["spell"]


async def test_hint_refused_after_midnight_straddle(client, world, monkeypatch):
    await three_wrong(client, world)

    async def straddle(kind, answer, guild_id, day, session):
        world.now += 86400  # midnight passes while the CDN answers
        return hints.HintContent("x", PNG)

    monkeypatch.setattr(game.hints, "render_hint", straddle)
    body = await (await post_hint(client, "spell")).json()
    assert body["status"] == "locked" and body["state"]["day"] == world.day.isoformat()
    assert world.buy_calls == [] and world.balance.get(USER, 1000) == 1000


async def test_letter_hint_appears_only_once_owned(client, world):
    await three_wrong(client, world)

    def letter(state):
        return state["hints"]["letter"]

    state = await (await client.get("/api/state", headers=auth())).json()
    assert "text" not in letter(state)
    await post_hint(client, "spell")
    await post_hint(client, "splash")
    body = await (await post_hint(client, "letter")).json()
    assert body["status"] == "bought"
    assert world.answer.name[0].upper() in letter(body["state"])["text"]
    state = await (await client.get("/api/state", headers=auth(OTHER))).json()
    assert "text" not in letter(state)


async def test_unbought_hint_image_is_403(client, world):
    await three_wrong(client, world)
    r = await client.get("/api/hints/spell.png", params={"ticket": ticket("hint:spell")})
    assert r.status == 403
    await post_hint(client, "spell")
    r = await client.get("/api/hints/spell.png", params={"ticket": ticket("hint:spell")})
    assert r.status == 200 and await r.read() == PNG
    assert r.headers["Content-Type"] == "image/png" and r.headers["Cache-Control"] == "private, no-store"
    assert "Content-Disposition" not in r.headers
    r = await client.get("/api/hints/spell.png", params={"ticket": ticket("hint:spell", OTHER)})
    assert r.status == 403


async def test_hint_image_ticket_rules(client, world):
    await three_wrong(client, world)
    await post_hint(client, "spell")
    assert (await client.get("/api/hints/spell.png")).status == 401
    assert (await client.get("/api/hints/spell.png", params={"ticket": "junk"})).status == 401
    assert (await client.get("/api/hints/spell.png", params={"ticket": ticket("hint:splash")})).status == 401
    assert (await client.get("/api/hints/letter.png", params={"ticket": ticket("hint:letter")})).status == 404
    assert (await client.get("/api/hints/spell.png", headers=auth())).status == 401


async def test_hint_image_cache_is_per_day_and_pruned(client, world):
    await three_wrong(client, world)
    await post_hint(client, "spell")
    cache = client.app[HINT_CACHE]
    assert list(cache) == [(world.day, "spell")]
    world.now += 86400
    r = await client.get("/api/hints/spell.png", params={"ticket": ticket("hint:spell", now=world.now)})
    assert r.status == 403
    assert list(cache) == []


async def test_splash_only_after_solving(client, world):
    await three_wrong(client, world)
    r = await client.get("/api/result/splash.png", params={"ticket": ticket("splash")})
    assert r.status == 403 and world.splash_calls == 0
    await guess(client, world.answer.name)
    r = await client.get("/api/result/splash.png", params={"ticket": ticket("splash")})
    assert r.status == 200 and await r.read() == PNG
    assert r.headers["Cache-Control"] == "private, no-store"
    assert (await client.get("/api/result/splash.png")).status == 401


async def test_splash_cdn_failure_is_502(client, world, monkeypatch):
    async def boom(answer, http):
        raise hints.HintUnavailable("x")

    await guess(client, world.answer.name)
    monkeypatch.setattr(game.assets, "render_splash", boom)
    r = await client.get("/api/result/splash.png", params={"ticket": ticket("splash")})
    assert r.status == 502 and await r.json() == {"error": "upstream"}


async def test_icon_allowlist_rejects_unknown_and_traversal(client, world):
    for bad in ("Nope", "..%2Fx", "http:", "aatrox", "%2e%2e", "Aatrox%2F..%2F"):
        r = await client.get(f"/api/icons/{bad}.png")
        assert r.status == 404, bad
    assert world.http_urls == []


async def test_icon_is_fetched_once_and_needs_no_session(client, world):
    r = await client.get("/api/icons/Aatrox.png")
    assert r.status == 200 and r.headers["Content-Type"] == "image/png"
    assert r.headers["Cache-Control"].startswith("public")
    assert (await r.read()).startswith(b"\x89PNG")
    await client.get("/api/icons/Aatrox.png")
    assert world.http_urls == ["https://cdn.communitydragon.org/latest/champion/Aatrox/square"]


async def test_icon_cache_is_bounded(client, monkeypatch):
    monkeypatch.setattr(catalog, "load", lambda: {"Aatrox": 1, "Ahri": 2})
    for name in ("Aatrox", "Ahri", "Aatrox"):
        assert (await client.get(f"/api/icons/{name}.png")).status == 200
    assert len(client.app[ICON_CACHE]) <= 2


async def test_icon_cdn_failure_is_502(client):
    client.app[HTTP].status = 500
    assert (await client.get("/api/icons/Ahri.png")).status == 502


async def test_write_rate_limit_answers_429(client, world):
    async def raw():
        return (await client.post("/api/hints/spell", headers=auth())).status

    statuses = [await raw() for _ in range(7)]
    assert statuses[:5] == [200] * 5 and statuses[5:] == [429, 429]
    r = await client.post("/api/guess", json={"champion": "x"}, headers=auth())
    assert r.status == 429 and await r.json() == {"error": "rate_limited"}
    assert (await client.post("/api/tickets", json={"resource": "ws"}, headers=auth())).status == 429
    assert (await client.get("/api/state", headers=auth())).status == 200  # reads are free
    world.now += 2
    assert await raw() == 200


async def test_live_players_and_online(world):
    client = await make_client(world, online=lambda: {OTHER})
    client.world = world
    world.players[OTHER] = ("Bob", "avhash")
    await guess(client, world.wrong[0].name)
    await guess(client, world.wrong[1].name, OTHER)
    live = (await (await client.get("/api/state", headers=auth())).json())["live"]
    assert [p["user_id"] for p in live] == [str(USER), str(OTHER)]
    me, bob = live
    assert me["name"] == "Joueur" and me["avatar"] is None and me["online"] is False
    assert bob["name"] == "Bob" and bob["avatar"] == "avhash" and bob["online"] is True
    assert bob["tries"] == 1 and len(bob["last_cells"]) == rules.COLUMN_COUNT and bob["solved"] is False
    await client.close()


async def test_online_member_without_a_guess_is_listed(world):
    client = await make_client(world, online=lambda: {USER, OTHER, 77})
    client.world = world
    world.players[OTHER] = ("Bob", "avhash")
    await guess(client, world.wrong[0].name)
    live = (await (await client.get("/api/state", headers=auth())).json())["live"]
    assert [p["user_id"] for p in live] == [str(USER), str(OTHER), "77"]
    bob, nobody = live[1], live[2]
    assert bob == {
        "user_id": str(OTHER),
        "name": "Bob",
        "avatar": "avhash",
        "tries": 0,
        "solved": False,
        "last_cells": [],
        "online": True,
    }
    assert nobody["name"] == "Joueur" and nobody["avatar"] is None and nobody["online"] is True
    text = json.dumps(live)
    assert world.answer.name not in text and world.answer.id not in text
    await client.close()


async def test_unknown_api_path_still_404(client):
    assert (await client.get("/api/nothing", headers=auth())).status == 404


def noisy_png(side: int = 120) -> bytes:
    """A PNG far bigger than one 4 KiB chunk (random pixels do not compress)."""
    import os

    out = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(out, "PNG")
    return out.getvalue()


async def test_multi_chunk_icon_is_not_truncated(client):
    body = noisy_png()
    assert len(body) > 4096 * 3
    client.app[HTTP].body = body
    r = await client.get("/api/icons/Ahri.png")
    assert r.status == 200
    assert Image.open(io.BytesIO(await r.read())).size == (120, 120)


async def test_oversized_icon_is_rejected(client, monkeypatch):
    monkeypatch.setattr(assets, "ICON_MAX_BYTES", 10_000)
    client.app[HTTP].body = noisy_png()
    assert (await client.get("/api/icons/Ahri.png")).status == 502


async def test_concurrent_requests_share_one_upstream_call(client, world):
    gate = asyncio.Event()
    client.app[HTTP].gate = gate
    tasks = [asyncio.ensure_future(client.get("/api/icons/Ahri.png")) for _ in range(20)]
    await asyncio.sleep(0.2)
    gate.set()
    assert {(await t).status for t in tasks} == {200}
    assert len(world.http_urls) == 1


async def test_cancelled_client_does_not_cancel_the_shared_fetch(client, world):
    gate = asyncio.Event()
    client.app[HTTP].gate = gate
    first = asyncio.ensure_future(client.get("/api/icons/Ahri.png"))
    await asyncio.sleep(0.1)
    second = asyncio.ensure_future(client.get("/api/icons/Ahri.png"))
    await asyncio.sleep(0.1)
    first.cancel()
    await asyncio.sleep(0.05)
    gate.set()
    assert (await second).status == 200
    assert len(world.http_urls) == 1


async def test_failure_cooldown_then_retry(client, world):
    client.app[HTTP].status = 500
    assert (await client.get("/api/icons/Ahri.png")).status == 502
    assert len(world.http_urls) == 1
    client.app[HTTP].status = 200
    world.now += 59
    assert (await client.get("/api/icons/Ahri.png")).status == 502
    assert len(world.http_urls) == 1  # within the cooldown: no upstream call
    world.now += 2
    assert (await client.get("/api/icons/Ahri.png")).status == 200
    assert len(world.http_urls) == 2


async def test_upstream_icon_fetches_are_capped(client, world):
    peak = current = 0
    gate = asyncio.Event()
    real = client.app[HTTP].get

    class Probe:
        def __init__(self, inner):
            self.inner = inner

        async def __aenter__(self):
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            return await self.inner.__aenter__()

        async def __aexit__(self, *a):
            nonlocal current
            current -= 1
            return False

    client.app[HTTP].gate = gate
    client.app[HTTP].get = lambda url, **kw: Probe(real(url, **kw))
    ids = sorted(catalog.load())[:6]
    tasks = [asyncio.ensure_future(client.get(f"/api/icons/{i}.png")) for i in ids]
    await asyncio.sleep(0.3)
    gate.set()
    assert {(await t).status for t in tasks} == {200}
    assert peak == 4


async def test_owned_hint_is_answered_without_downloading(client, world):
    await three_wrong(client, world)
    await post_hint(client, "spell")
    world.render_calls.clear()
    world.buy_calls.clear()
    assert (await (await post_hint(client, "spell")).json())["status"] == "owned"
    assert (await (await post_hint(client, "letter")).json())["status"] == "locked"
    assert world.render_calls == [] and world.buy_calls == []


async def test_cached_render_is_reused_for_the_next_buyer(client, world):
    await three_wrong(client, world)
    for u in (USER, OTHER):
        for c in world.wrong[:3]:
            await guess(client, c.name, u)
    await post_hint(client, "spell", USER)
    world.render_calls.clear()
    assert (await (await post_hint(client, "spell", OTHER)).json())["status"] == "bought"
    assert world.render_calls == []

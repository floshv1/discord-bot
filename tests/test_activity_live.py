# ruff: noqa: F811  (pytest fixtures are imported, then used as parameters)
import asyncio
import json

import pytest

from activity.server import live
from activity.server.keys import HUB
from bot.cogs.loldle.events import Event
from tests.activity_game_fakes import GUILD, OTHER, USER, client, make_client, ticket, world  # noqa: F401


class FakeWs:
    def __init__(self, fail: bool = False, hang: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail, self.hang = fail, hang
        self.closed = False

    async def send_str(self, text: str) -> None:
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail:
            raise ConnectionResetError
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.closed = True


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def next_of(ws, kind: str) -> dict:
    """The next message of a type: a socket also hears its own arrival as presence."""
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), 2)
        if msg["type"] == kind:
            return msg


async def next_presence(ws, user_id: int) -> dict:
    while True:
        msg = await next_of(ws, "presence")
        if msg["user_id"] == str(user_id):
            return msg


async def next_player(ws, user_id: int) -> dict:
    while True:
        msg = await next_of(ws, "player")
        if msg["player"]["user_id"] == str(user_id):
            return msg


def types(ws: FakeWs) -> list[str]:
    return [m["type"] for m in ws.sent]


async def test_presence_counts_connections():
    hub, watcher = live.Hub(), FakeWs()
    assert hub.connect(1, watcher)
    await settle()
    a, b = FakeWs(), FakeWs()
    assert hub.connect(2, a) and hub.connect(2, b)
    await settle()
    hub.disconnect(2, a)
    await settle()
    assert hub.online() == {1, 2}  # still one socket open
    hub.disconnect(2, b)
    await settle()
    assert hub.online() == {1}
    presence = [m for m in watcher.sent if m["type"] == "presence" and m["user_id"] == "2"]
    assert [m["online"] for m in presence] == [True, False]  # 0->1 and 1->0 only


async def test_socket_cap_per_member():
    hub = live.Hub()
    assert all(hub.connect(1, FakeWs()) for _ in range(live.MAX_SOCKETS_PER_USER))
    assert hub.connect(1, FakeWs()) is False
    assert hub.connect(2, FakeWs()) is True


async def test_global_socket_cap(monkeypatch):
    monkeypatch.setattr(live, "MAX_SOCKETS", 2)
    hub = live.Hub()
    assert hub.connect(1, FakeWs()) and hub.connect(2, FakeWs())
    assert hub.connect(3, FakeWs()) is False


async def test_dead_socket_does_not_break_broadcast():
    hub, good, dead = live.Hub(), FakeWs(), FakeWs(fail=True)
    hub.connect(1, good)
    hub.connect(2, dead)
    await settle()
    await hub.broadcast({"type": "day"})
    await settle()
    assert "day" in types(good)
    assert hub.online() == {1} and dead.closed
    assert any(m == {"type": "presence", "user_id": "2", "online": False} for m in good.sent)


async def test_slow_socket_is_dropped_without_stalling_the_others(monkeypatch):
    monkeypatch.setattr(live, "SEND_TIMEOUT", 0.05)
    hub, good, slow = live.Hub(), FakeWs(), FakeWs(hang=True)
    hub.connect(1, good)
    hub.connect(2, slow)
    await settle()
    await asyncio.wait_for(hub.broadcast({"type": "day"}), 2)
    assert hub.online() == {1}


async def test_event_broadcasts_only_colours_and_counts(world):
    client = await make_client(world)
    try:
        world.guesses[USER] = []
        await client.post("/api/guess", json={"champion": world.wrong[0].name}, headers=_auth())
        ws = await client.ws_connect(f"/ws?ticket={ticket('ws')}")
        await settle()
        await live.push_player(client.app, Event(GUILD, world.day, USER, "guess"))
        msg = await next_of(ws, "player")
        assert set(msg["player"]) == {"user_id", "name", "avatar", "tries", "solved", "last_cells", "online"}
        assert msg["player"]["user_id"] == str(USER) and msg["player"]["online"] is True
        assert msg["player"]["last_cells"]
        text = json.dumps(msg)
        for champion in (world.answer, world.wrong[0]):
            assert champion.name not in text and champion.id not in text
    finally:
        await client.close()


async def test_arrival_without_a_guess_broadcasts_a_zero_row(world):
    client = await make_client(world)
    try:
        world.players[OTHER] = ("Bob", "avhash")
        first = await client.ws_connect(f"/ws?ticket={ticket('ws', USER)}")
        await settle()
        await client.ws_connect(f"/ws?ticket={ticket('ws', OTHER)}")
        msg = await next_player(first, OTHER)
        assert msg["player"] == {
            "user_id": str(OTHER),
            "name": "Bob",
            "avatar": "avhash",
            "tries": 0,
            "solved": False,
            "last_cells": [],
            "online": True,
        }
        assert world.answer.name not in json.dumps(msg)
    finally:
        await client.close()


async def test_arrival_of_a_player_who_guessed_broadcasts_the_real_row(world):
    client = await make_client(world)
    try:
        await client.post("/api/guess", json={"champion": world.wrong[0].name}, headers=_auth())
        watcher = await client.ws_connect(f"/ws?ticket={ticket('ws', OTHER)}")
        await settle()
        await client.ws_connect(f"/ws?ticket={ticket('ws', USER)}")
        msg = await next_player(watcher, USER)
        assert msg["player"]["user_id"] == str(USER) and msg["player"]["tries"] == 1 and msg["player"]["last_cells"]
    finally:
        await client.close()


async def test_second_socket_of_a_member_adds_no_arrival():
    hub, watcher = live.Hub(), FakeWs()
    calls = []

    async def arrival(user_id):
        calls.append(user_id)

    hub.arrival = arrival
    hub.connect(1, watcher)
    await settle()
    hub.connect(2, FakeWs())
    hub.connect(2, FakeWs())
    await settle()
    assert calls == [1, 2]


async def test_arrival_failure_still_sends_presence():
    hub, watcher = live.Hub(), FakeWs()

    async def arrival(user_id):
        raise RuntimeError("boom")

    hub.arrival = arrival
    hub.connect(1, watcher)
    await settle()
    assert types(watcher) == ["presence"]


async def test_player_message_uses_the_state_serializer(world):
    client = await make_client(world)
    try:
        await client.post("/api/guess", json={"champion": world.wrong[0].name}, headers=_auth())
        ws = await client.ws_connect(f"/ws?ticket={ticket('ws')}")
        await settle()
        await live.push_player(client.app, Event(GUILD, world.day, USER, "guess"))
        msg = await next_of(ws, "player")
        state = await (await client.get("/api/state", headers=_auth())).json()
        assert msg["player"] == state["live"][0]
    finally:
        await client.close()


async def test_event_from_another_guild_is_ignored(world):
    client = await make_client(world)
    try:
        await client.post("/api/guess", json={"champion": world.wrong[0].name}, headers=_auth())
        ws = await client.ws_connect(f"/ws?ticket={ticket('ws')}")
        await next_of(ws, "presence")
        await next_of(ws, "player")  # our own arrival row
        await live.push_player(client.app, Event(GUILD + 1, world.day, USER, "guess"))
        await live.push_player(client.app, Event(GUILD, world.day.replace(year=1999), USER, "guess"))
        await live.push_player(client.app, Event(GUILD, world.day, OTHER, "guess"))  # no row
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.receive_json(), 0.2)
    finally:
        await client.close()


async def test_ws_requires_a_ws_ticket(client):
    assert (await client.get("/ws")).status == 401
    assert (await client.get("/ws?ticket=garbage")).status == 401
    assert (await client.get(f"/ws?ticket={ticket('splash')}")).status == 401
    assert (await client.get(f"/ws?ticket={ticket('ws', now=1.0)}")).status == 401  # expired


async def test_ws_over_the_cap_is_closed_with_4008(client):
    socks = [await client.ws_connect(f"/ws?ticket={ticket('ws')}") for _ in range(live.MAX_SOCKETS_PER_USER)]
    extra = await client.ws_connect(f"/ws?ticket={ticket('ws')}")
    await extra.receive(timeout=2)
    assert extra.close_code == live.CLOSE_TOO_MANY
    assert socks[0].close_code is None


async def test_presence_over_real_websockets(client):
    watcher = await client.ws_connect(f"/ws?ticket={ticket('ws', OTHER)}")
    await settle()
    mine = await client.ws_connect(f"/ws?ticket={ticket('ws', USER)}")
    assert await next_presence(watcher, USER) == {"type": "presence", "user_id": str(USER), "online": True}
    await mine.close()
    assert await next_presence(watcher, USER) == {"type": "presence", "user_id": str(USER), "online": False}


async def test_client_messages_are_ignored_and_oversized_ones_close(client):
    ws = await client.ws_connect(f"/ws?ticket={ticket('ws')}")
    await ws.send_str("hello")
    await ws.send_str("x" * 2048)
    while (msg := await ws.receive(timeout=2)).type.name == "TEXT":
        pass  # our own presence
    assert msg.type.name in ("CLOSE", "CLOSING", "CLOSED")


async def test_online_comes_from_the_hub(client):
    ws = await client.ws_connect(f"/ws?ticket={ticket('ws')}")
    await settle()
    assert client.app[HUB].online() == {USER}
    await ws.close()


async def test_unconfigured_ws_answers_503(world):
    from aiohttp.test_utils import TestClient, TestServer

    from activity.server import app as app_mod

    c = TestClient(TestServer(app_mod.create_app(None, listen=False)))
    await c.start_server()
    try:
        assert (await c.get("/ws")).status == 503
    finally:
        await c.close()


async def test_day_change_is_broadcast(world, monkeypatch):
    monkeypatch.setattr(live, "DAY_CHECK_SECONDS", 0.02)
    client = await make_client(world)
    try:
        ws = await client.ws_connect(f"/ws?ticket={ticket('ws')}")
        await settle()
        world.now += 86400 * 2
        got = []
        while {"type": "day"} not in got:
            got.append(await asyncio.wait_for(ws.receive_json(), 2))
    finally:
        await client.close()


async def test_listener_reconnect_triggers_a_reload(world, monkeypatch):
    captured = {}

    class FakeListener:
        def __init__(self, dsn, on_event, on_connect, **kw):
            captured["on_connect"] = on_connect
            captured["kw"] = kw

        def start(self):
            pass

        async def stop(self):
            captured["stopped"] = True

    monkeypatch.setattr(live, "EventListener", FakeListener)
    from aiohttp.test_utils import TestClient, TestServer

    from activity.server import app as app_mod
    from tests.activity_game_fakes import SETTINGS, FakeHttp

    async def fine():
        return None

    app = app_mod.create_app(SETTINGS, http=FakeHttp(world), clock=lambda: world.now, ping=fine)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        ws = await c.ws_connect(f"/ws?ticket={ticket('ws')}")
        await settle()
        await captured["on_connect"]()  # first connection: nothing missed
        await captured["on_connect"]()  # reconnection: reload
        assert await next_of(ws, "day") == {"type": "day"}
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.receive_json(), 0.1)
    finally:
        await c.close()
    assert captured["stopped"]
    assert captured["kw"] == {"health_interval": live.LISTENER_HEALTH_SECONDS}  # the half-open safety net


def _auth():
    from tests.activity_game_fakes import auth

    return auth()


async def test_closed_hub_refuses_new_sockets():
    hub = live.Hub()
    await hub.close()
    assert hub.connect(1, FakeWs()) is False


async def test_open_socket_does_not_block_shutdown(world):
    client = await make_client(world)
    ws = await client.ws_connect(f"/ws?ticket={ticket('ws')}")
    await settle()
    await asyncio.wait_for(client.server.close(), 5)  # runs on_shutdown, then waits for handlers
    msg = await ws.receive(timeout=2)
    while msg.type.name == "TEXT":
        msg = await ws.receive(timeout=2)
    assert msg.type.name in ("CLOSE", "CLOSING", "CLOSED")
    await client.close()

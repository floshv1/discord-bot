"""An in-memory stand-in for the LoLdle/currency persistence, for the Activity's game routes.

Only the persistence is faked: routes, rules, catalog, rendering and the HTTP stack are the real ones.
Real SQL is exercised separately against a real Postgres.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from activity.server import app as app_mod
from activity.server import game
from activity.server.session import issue, issue_ticket
from activity.server.settings import Settings
from bot.cogs.loldle import catalog, hints, rules, service

SECRET = "test-session-secret-0123456789abcdef"
GUILD = 123456789012345678
SETTINGS = Settings("cid", "csecret", GUILD, "postgresql://x", SECRET)
NOW = 1_000_000.0
USER = 42
OTHER = 43
PNG = b"\x89PNG-fake"


class World:
    def __init__(self) -> None:
        self.now = NOW
        champs = sorted(catalog.load().values(), key=lambda c: c.id)
        self.answer = champs[0]
        self.wrong = [c for c in champs if c.id != self.answer.id]
        self.guesses: dict[int, list[dict]] = {}
        self.results: dict[int, dict] = {}
        self.owned: dict[int, set[str]] = {}
        self.balance: dict[int, int] = {}
        self.players: dict[int, tuple[str, str | None]] = {}
        self.buy_calls: list[str] = []
        self.owned_day: dt.date | None = None  # the fake keeps hints for one day only
        self.owned_day = self.day
        self.render_calls: list[str] = []
        self.render_fail = False
        self.splash_calls = 0
        self.http_urls: list[str] = []

    @property
    def day(self) -> dt.date:
        return rules.paris_day(dt.datetime.fromtimestamp(self.now, dt.UTC))


class FakeResp:
    """Delivers its body in small chunks, like a socket: `read(n)` returns only what is buffered."""

    CHUNK = 4096

    def __init__(self, body: bytes, status: int = 200, gate: asyncio.Event | None = None) -> None:
        self.status = status
        self._body = body
        self._gate = gate
        self.content = SimpleNamespace(read=self._read, iter_chunked=self._iter)

    async def _read(self, n=-1):
        return self._body[: min(n, self.CHUNK)] if n >= 0 else self._body

    async def _iter(self, n):
        for i in range(0, len(self._body), min(n, self.CHUNK)):
            yield self._body[i : i + min(n, self.CHUNK)]

    async def read(self):
        return self._body

    async def __aenter__(self):
        if self._gate is not None:
            await self._gate.wait()
        return self

    async def __aexit__(self, *a):
        return False


def png_bytes(size: int = 8) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (size, size), (200, 10, 10)).save(out, "PNG")
    return out.getvalue()


class FakeHttp:
    def __init__(self, world: World) -> None:
        self.world = world
        self.status = 200
        self.body: bytes | None = None
        self.gate: asyncio.Event | None = None

    def get(self, url, **kw):
        self.world.http_urls.append(url)
        return FakeResp(self.body if self.body is not None else png_bytes(), self.status, self.gate)

    async def close(self):
        pass


@pytest.fixture
def world(monkeypatch) -> World:
    w = World()

    async def get_or_pick_day(guild_id, day, champions):
        return {"answer": json.dumps(w.answer.to_json()), "champion_id": w.answer.id}

    async def guesses(guild_id, day, user_id):
        return w.guesses.get(user_id, [])

    async def result(guild_id, day, user_id):
        return w.results.get(user_id)

    async def owned(guild_id, day, user_id):
        return set(w.owned.get(user_id, set())) if day == w.owned_day else set()

    async def record_guess(guild_id, day, user_id, guess, answer):
        rows = w.guesses.setdefault(user_id, [])
        if user_id in w.results:
            return service.GuessOutcome("solved_already")
        if any(r["champion_id"] == guess.id for r in rows):
            return service.GuessOutcome("duplicate")
        cells = rules.compare(guess, answer)
        rows.append(
            {
                "number": len(rows) + 1,
                "champion_id": guess.id,
                "name": guess.name,
                "cells": json.dumps(cells),
                "clues": json.dumps(rules.clues(guess)),
            }
        )
        if guess.id == answer.id:
            used = len(w.owned.get(user_id, ()))
            w.results[user_id] = {"tries": len(rows), "hints": used, "score": rules.score(len(rows), used)}
            return service.GuessOutcome("ok", cells, len(rows), True)
        return service.GuessOutcome("ok", cells, len(rows))

    async def buy_hint(guild_id, day, user_id, kind):
        w.buy_calls.append(kind)
        w.owned_day = day
        owned_now = w.owned.setdefault(user_id, set())
        if kind in owned_now:
            return service.BuyOutcome("owned")
        if user_id in w.results or not rules.can_buy(kind, owned_now, len(w.guesses.get(user_id, []))):
            return service.BuyOutcome("locked")
        bal = w.balance.get(user_id, 1000)
        if bal < rules.HINTS[kind]:
            return service.BuyOutcome("poor", bal)
        w.balance[user_id] = bal - rules.HINTS[kind]
        owned_now.add(kind)
        return service.BuyOutcome("bought", w.balance[user_id])

    async def wallet(guild_id, user_id):
        return {"balance": w.balance.setdefault(user_id, 1000)}, False

    async def live_rows(guild_id, day):
        out = []
        for uid, rows in sorted(w.guesses.items()):
            name = w.players.get(uid, (str(uid), None))
            out.append(
                {
                    "user_id": uid,
                    "display_name": name[0],
                    "avatar_hash": name[1],
                    "tries": len(rows),
                    "solved": uid in w.results,
                    "last_cells": rows[-1]["cells"],
                }
            )
        return out

    async def player_names(guild_id, user_ids):
        return [
            {"user_id": u, "display_name": w.players[u][0], "avatar_hash": w.players[u][1]}
            for u in user_ids
            if u in w.players
        ]

    async def no_days(guild_id):
        return []

    async def solved(guild_id, user_id):
        return {w.day} if user_id in w.results else set()

    real_render = hints.render_hint

    async def render(kind, answer, guild_id, day, session):
        w.render_calls.append(kind)
        if kind == "letter":
            return await real_render(kind, answer, guild_id, day, session)
        if w.render_fail:
            raise hints.HintUnavailable("boom")
        return hints.HintContent("x", PNG)

    async def splash(answer, http):
        w.splash_calls += 1
        return PNG

    monkeypatch.setattr(service, "get_or_pick_day", get_or_pick_day)
    monkeypatch.setattr(service, "guesses", guesses)
    monkeypatch.setattr(service, "result", result)
    monkeypatch.setattr(service, "hints", owned)
    monkeypatch.setattr(service, "record_guess", record_guess)
    monkeypatch.setattr(service, "buy_hint", buy_hint)
    monkeypatch.setattr(service, "live_rows", live_rows)
    monkeypatch.setattr(service, "player_names", player_names)
    monkeypatch.setattr(service, "all_days", no_days)
    monkeypatch.setattr(service, "solved_days", solved)
    monkeypatch.setattr(game.currency, "get_or_create_wallet", wallet)
    monkeypatch.setattr(game.hints, "render_hint", render)
    monkeypatch.setattr(game.assets, "render_splash", splash)
    return w


async def make_client(world: World, **kw) -> TestClient:
    async def fine():
        return None

    app = app_mod.create_app(
        SETTINGS, http=FakeHttp(world), clock=lambda: world.now, ping=fine, **{"listen": False, **kw}
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def auth(user: int = USER) -> dict:
    return {"Authorization": f"Bearer {issue(SECRET, user, NOW)}"}


def ticket(resource: str, user: int = USER, now: float = NOW) -> str:
    return issue_ticket(SECRET, user, resource, now)


@pytest.fixture
async def client(world):
    c = await make_client(world)
    c.world = world
    try:
        yield c
    finally:
        await c.close()

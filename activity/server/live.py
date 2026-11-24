"""The room in real time: one WebSocket per open Activity, fed by Postgres NOTIFY.

Messages carry only `LivePlayer` fields, presence or a day change: never a champion name,
so nothing here can leak the answer. The server ignores whatever a client sends.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import Awaitable, Callable

import aiohttp
from aiohttp import web
from loguru import logger

from activity.server import game, session
from activity.server.auth import error
from activity.server.keys import CLOCK, HUB, SETTINGS
from bot.cogs.loldle import rules, service
from bot.cogs.loldle.events import Event, EventListener

MAX_SOCKETS_PER_USER = 3
MAX_SOCKETS = 200
SEND_TIMEOUT = 5.0
DAY_CHECK_SECONDS = 30.0
LISTENER_HEALTH_SECONDS = 60.0
CLOSE_TIMEOUT = 2.0
WS_MAX_MSG = 1024
WS_HEARTBEAT = 30
CLOSE_TOO_MANY = 4008


class Hub:
    def __init__(self) -> None:
        self._sockets: dict[int, set] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        # Set by `setup`: builds a member's panel row when they arrive (None: nothing to add).
        self.arrival: Callable[[int], Awaitable[dict | None]] | None = None

    def __len__(self) -> int:
        return sum(len(s) for s in self._sockets.values())

    def online(self) -> set[int]:
        return set(self._sockets)

    def connect(self, user_id: int, ws) -> bool:
        """False when over a cap: the caller closes the socket. Presence moves on 0 -> 1 only."""
        if self._closed:  # shutting down: a handshake still in flight must not register
            return False
        mine = self._sockets.get(user_id)
        if (mine is not None and len(mine) >= MAX_SOCKETS_PER_USER) or len(self) >= MAX_SOCKETS:
            return False
        if mine is None:
            self._sockets[user_id] = {ws}
            self._announce(user_id, True)
        else:
            mine.add(ws)
        return True

    def disconnect(self, user_id: int, ws) -> None:
        mine = self._sockets.get(user_id)
        if mine is None or ws not in mine:
            return
        mine.discard(ws)
        if not mine:
            del self._sockets[user_id]
            self._announce(user_id, False)

    def _announce(self, user_id: int, online: bool) -> None:
        task = asyncio.ensure_future(self._announce_async(user_id, online))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _announce_async(self, user_id: int, online: bool) -> None:
        await self.broadcast({"type": "presence", "user_id": str(user_id), "online": online})
        if not online or self.arrival is None:
            return
        # A member with the Activity open belongs in everyone's panel even before a first guess.
        try:
            player = await self.arrival(user_id)
        except Exception as exc:  # noqa: BLE001 - presence already went out; the front refreshes anyway
            logger.warning("live: arrival row failed: {}", type(exc).__name__)
            return
        if player is not None and user_id in self._sockets:
            await self.broadcast({"type": "player", "player": player})

    async def _send(self, user_id: int, ws, text: str) -> None:
        try:
            await asyncio.wait_for(ws.send_str(text), SEND_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - a slow or dead socket is dropped, never fatal
            logger.info("live: dropping a socket ({})", type(exc).__name__)
            self.disconnect(user_id, ws)
            await self._close(ws)

    @staticmethod
    async def _close(ws, code: int = aiohttp.WSCloseCode.GOING_AWAY) -> None:
        try:
            await asyncio.wait_for(ws.close(code=code), CLOSE_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass

    async def broadcast(self, message: dict) -> None:
        text = json.dumps(message)
        targets = [(uid, ws) for uid, socks in self._sockets.items() for ws in socks]
        if targets:
            await asyncio.gather(*(self._send(uid, ws, text) for uid, ws in targets))

    async def close(self) -> None:
        self._closed = True
        sockets = [ws for socks in self._sockets.values() for ws in socks]
        self._sockets.clear()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if sockets:
            await asyncio.gather(*(self._close(ws) for ws in sockets))


async def ws_handler(request: web.Request) -> web.StreamResponse:
    app = request.app
    user_id = session.verify_ticket(app[SETTINGS].session_secret, request.query.get("ticket"), "ws", app[CLOCK]())
    if user_id is None:
        return error(401, "unauthorized")
    ws = web.WebSocketResponse(max_msg_size=WS_MAX_MSG, heartbeat=WS_HEARTBEAT)
    await ws.prepare(request)
    hub: Hub = app[HUB]
    if not hub.connect(user_id, ws):
        await Hub._close(ws, CLOSE_TOO_MANY)
        return ws
    try:
        async for _ in ws:  # the server ignores every client message
            pass
    finally:
        hub.disconnect(user_id, ws)
    return ws


def _today(clock: Callable[[], float]) -> dt.date:
    return rules.paris_day(dt.datetime.fromtimestamp(clock(), dt.UTC))


async def push_player(app: web.Application, event: Event) -> None:
    settings = app[SETTINGS]
    if event.guild_id != settings.guild_id or event.day != _today(app[CLOCK]):
        return
    hub: Hub = app[HUB]
    for row in await service.live_rows(settings.guild_id, event.day):
        if row["user_id"] == event.user_id:
            await hub.broadcast({"type": "player", "player": game.live_player(row, hub.online())})
            return


async def arrival_player(app: web.Application, user_id: int) -> dict | None:
    """The row a member who just opened the Activity shows: their real one if they have guessed."""
    settings = app[SETTINGS]
    if settings is None:
        return None
    hub: Hub = app[HUB]
    day = _today(app[CLOCK])
    for row in await service.live_rows(settings.guild_id, day):
        if row["user_id"] == user_id:
            return game.live_player(row, hub.online())
    names = await service.player_names(settings.guild_id, [user_id])
    return game.idle_player(user_id, names[0] if names else None, hub.online())


async def watch_day(app: web.Application) -> None:
    """Tell every client to reload when the Paris day turns. Never raises."""
    hub: Hub = app[HUB]
    last = _today(app[CLOCK])
    while True:
        await asyncio.sleep(DAY_CHECK_SECONDS)
        try:
            current = _today(app[CLOCK])
            if current != last:
                last = current
                await hub.broadcast({"type": "day"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("live: day watch failed: {}", type(exc).__name__)


def setup(app: web.Application, hub: Hub, *, listen: bool) -> None:
    app[HUB] = hub
    hub.arrival = lambda user_id: arrival_player(app, user_id)
    app.router.add_get("/ws", ws_handler)

    async def live_ctx(app: web.Application):
        if app[SETTINGS] is None:
            yield
            return
        connected = False

        async def on_event(event: Event) -> None:
            await push_player(app, event)

        async def on_connect() -> None:
            nonlocal connected
            if connected:  # events may have been missed while the connection was down
                await hub.broadcast({"type": "day"})
            connected = True

        listener = (
            EventListener(app[SETTINGS].database_url, on_event, on_connect, health_interval=LISTENER_HEALTH_SECONDS)
            if listen
            else None
        )
        if listener is not None:
            listener.start()
        day_task = asyncio.create_task(watch_day(app))
        try:
            yield
        finally:
            day_task.cancel()
            await asyncio.gather(day_task, return_exceptions=True)
            if listener is not None:
                await listener.stop()

    async def on_shutdown(app: web.Application) -> None:
        # Before aiohttp waits on in-flight handlers: an open socket keeps its handler alive.
        await hub.close()

    app.on_shutdown.append(on_shutdown)
    app.cleanup_ctx.append(live_ctx)

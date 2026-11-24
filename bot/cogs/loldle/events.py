"""Listens to `loldle_events` on a dedicated connection and reconnects when it drops.

The pool cannot do this: LISTEN needs a connection that stays out of rotation. A dropped
connection loses notifications, so `on_connect` fires after every (re)connection and the
consumer resyncs from the database instead of trusting what it heard.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg
from loguru import logger

CHANNEL = "loldle_events"


@dataclass(frozen=True)
class Event:
    guild_id: int
    day: dt.date
    user_id: int
    kind: str


def parse(payload: str) -> Event | None:
    try:
        data = json.loads(payload)
        return Event(int(data["guild_id"]), dt.date.fromisoformat(data["day"]), int(data["user_id"]), str(data["kind"]))
    except (ValueError, KeyError, TypeError):
        return None


class EventListener:
    def __init__(
        self,
        dsn: str,
        on_event: Callable[[Event], Awaitable[None]],
        on_connect: Callable[[], Awaitable[None]],
        *,
        backoff: float = 5.0,
        health_interval: float | None = None,
        health_timeout: float = 5.0,
    ) -> None:
        self._dsn = dsn
        self._on_event = on_event
        self._on_connect = on_connect
        self._backoff = backoff
        # A half-open socket never reports termination: with an interval set, the connection is
        # probed with `SELECT 1` and torn down (so the reconnect runs) when the probe fails.
        self._health_interval = health_interval
        self._health_timeout = health_timeout
        self._task: asyncio.Task | None = None
        self._lost = asyncio.Event()
        self._pending: set[asyncio.Task] = set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _notified(self, _conn, _pid, _channel, payload: str) -> None:
        event = parse(payload)
        if event is None:
            logger.warning("loldle: ignoring a malformed event payload")
            return
        job = asyncio.create_task(self._dispatch(event))
        self._pending.add(job)
        job.add_done_callback(self._pending.discard)

    async def _dispatch(self, event: Event) -> None:
        try:
            await self._on_event(event)
        except Exception as err:
            logger.warning(f"loldle: event handler failed: {type(err).__name__}")

    def _terminated(self, _conn) -> None:
        self._lost.set()

    async def _watch(self, conn) -> None:
        """Wait until the connection is lost, probing it every `health_interval` seconds if asked to."""
        if self._health_interval is None:
            await self._lost.wait()
            return
        while not self._lost.is_set():
            try:
                await asyncio.wait_for(self._lost.wait(), self._health_interval)
                return
            except TimeoutError:
                pass
            try:
                await conn.execute("SELECT 1", timeout=self._health_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.warning(f"loldle: event connection health check failed: {type(err).__name__}")
                try:
                    conn.terminate()
                except Exception:
                    pass
                self._lost.set()

    async def _run(self) -> None:
        while True:
            conn = None
            try:
                conn = await asyncpg.connect(self._dsn)
                self._lost.clear()
                conn.add_termination_listener(self._terminated)
                await conn.add_listener(CHANNEL, self._notified)
                await self._on_connect()
                await self._watch(conn)
                logger.warning("loldle: event connection lost, reconnecting")
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.warning(f"loldle: event listener failed: {type(err).__name__}")
            finally:
                if conn is not None and not conn.is_closed():
                    try:
                        await conn.close(timeout=2)
                    except Exception:
                        pass
            await asyncio.sleep(self._backoff)

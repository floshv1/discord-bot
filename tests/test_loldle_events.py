import asyncio
import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.loldle import events

PAYLOAD = json.dumps({"guild_id": 1, "day": "2026-11-23", "user_id": 2, "kind": "win"})


def _conn():
    c = MagicMock()
    c.add_listener = AsyncMock()
    c.add_termination_listener = MagicMock()
    c.is_closed = MagicMock(return_value=False)
    c.close = AsyncMock()
    return c


async def test_listener_reconnects_and_signals():
    first, second = _conn(), _conn()
    connects = iter([first, second])
    got, connected = [], []

    async def on_event(e):
        got.append(e)

    async def on_connect():
        connected.append(1)

    listener = events.EventListener("dsn", on_event, on_connect, backoff=0)
    with patch.object(events.asyncpg, "connect", AsyncMock(side_effect=lambda dsn: next(connects))):
        listener.start()
        for _ in range(20):
            await asyncio.sleep(0)
        first.add_termination_listener.call_args.args[0](first)  # terminated
        for _ in range(50):
            await asyncio.sleep(0)
        assert len(connected) == 2
        second.add_listener.await_args.args[1](second, 1, events.CHANNEL, PAYLOAD)
        for _ in range(10):
            await asyncio.sleep(0)
        await listener.stop()
    assert got == [events.Event(1, dt.date(2026, 11, 23), 2, "win")]
    assert second.add_listener.await_args.args[0] == "loldle_events"


async def test_listener_ignores_malformed_payload():
    conn = _conn()
    got = []

    async def on_event(e):
        got.append(e)

    listener = events.EventListener("dsn", on_event, AsyncMock(), backoff=0)
    with patch.object(events.asyncpg, "connect", AsyncMock(return_value=conn)):
        listener.start()
        for _ in range(20):
            await asyncio.sleep(0)
        notified = conn.add_listener.await_args.args[1]
        for bad in ("nope", "{}", '{"guild_id": "x"}', "[1]"):
            notified(conn, 1, events.CHANNEL, bad)
        for _ in range(10):
            await asyncio.sleep(0)
        await listener.stop()
    assert got == []


async def test_failed_health_check_forces_a_reconnect():
    first, second = _conn(), _conn()
    first.execute = AsyncMock(side_effect=TimeoutError)
    second.execute = AsyncMock()
    first.terminate = MagicMock()
    connects = iter([first, second])
    connected = []

    async def on_connect():
        connected.append(1)

    listener = events.EventListener("dsn", AsyncMock(), on_connect, backoff=0, health_interval=0.01, health_timeout=0.5)
    with patch.object(events.asyncpg, "connect", AsyncMock(side_effect=lambda dsn: next(connects))):
        listener.start()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if len(connected) == 2:
                break
        await listener.stop()
    assert len(connected) == 2
    first.execute.assert_awaited_with("SELECT 1", timeout=0.5)
    first.terminate.assert_called_once()


async def test_healthy_connection_is_not_recycled_and_no_probe_by_default():
    conn = _conn()
    conn.execute = AsyncMock()
    connected = []

    async def on_connect():
        connected.append(1)

    listener = events.EventListener("dsn", AsyncMock(), on_connect, backoff=0, health_interval=0.01)
    with patch.object(events.asyncpg, "connect", AsyncMock(return_value=conn)):
        listener.start()
        await asyncio.sleep(0.1)
        await listener.stop()
    assert connected == [1] and conn.execute.await_count >= 2

    quiet = _conn()
    quiet.execute = AsyncMock()
    listener = events.EventListener("dsn", AsyncMock(), AsyncMock(), backoff=0)
    with patch.object(events.asyncpg, "connect", AsyncMock(return_value=quiet)):
        listener.start()
        await asyncio.sleep(0.05)
        await listener.stop()
    quiet.execute.assert_not_called()

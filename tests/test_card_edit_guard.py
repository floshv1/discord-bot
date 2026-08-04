"""The content guard that stops a fixed-interval ticker becoming a PATCH storm.

The bot once spent an afternoon editing one message every ~5.5 seconds without pause. The
mechanism: `discord.ext.tasks` schedules the next iteration from the previous *scheduled*
time, so a body that overruns its interval re-fires with zero delay and never catches up —
and the Now-Playing card was redrawn unconditionally every tick, with a progress bar making
every payload unique so Discord never no-opped it. These tests lock the two halves of the
fix: don't send an edit nobody would see, and stop entirely once Discord says 429.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.core import discord_utils
from bot.core.discord_utils import RATE_LIMIT_BACKOFF, edit_if_changed


class _Response:
    def __init__(self, status):
        self.status = status
        self.reason = "Too Many Requests" if status == 429 else "Error"


def _message(message_id=1):
    message = AsyncMock(spec=discord.PartialMessage)
    message.id = message_id
    return message


def _embed(description="same"):
    return discord.Embed(title="Card", description=description)


@pytest.mark.asyncio
async def test_first_render_is_sent():
    message = _message()
    assert await edit_if_changed(message, label="test", embed=_embed()) is True
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_identical_render_is_skipped():
    # The whole point: a ticker that fires every 10s on a card that hasn't moved costs zero
    # API calls instead of six a minute, forever.
    message = _message()
    await edit_if_changed(message, label="test", embed=_embed())
    assert await edit_if_changed(message, label="test", embed=_embed()) is False
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_changed_render_is_sent():
    message = _message()
    await edit_if_changed(message, label="test", embed=_embed("before"))
    assert await edit_if_changed(message, label="test", embed=_embed("after")) is True
    assert message.edit.await_count == 2


@pytest.mark.asyncio
async def test_a_new_timestamp_alone_is_not_a_change():
    # Panels stamp "last checked" on every tick. Rendering that is not worth a request, and
    # counting it as a change would defeat the guard entirely — an idle Palworld server would
    # keep paying two edits a minute to redraw an identical card with a newer clock on it.
    message = _message()
    first = _embed()
    first.timestamp = discord.utils.utcnow()
    await edit_if_changed(message, label="test", embed=first)

    later = _embed()
    later.timestamp = discord.utils.utcnow() + datetime.timedelta(minutes=5)
    assert await edit_if_changed(message, label="test", embed=later) is False
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_changed_view_is_sent_even_when_the_embed_matches():
    # The Palworld panel swaps Start for Stop without touching the embed's body; a guard that
    # only looked at the embed would leave the wrong button on the card.
    message = _message()
    view = MagicMock(spec=discord.ui.View)
    view.to_components = MagicMock(return_value=[{"label": "Start"}])
    await edit_if_changed(message, label="test", embed=_embed(), view=view)

    view.to_components = MagicMock(return_value=[{"label": "Stop"}])
    assert await edit_if_changed(message, label="test", embed=_embed(), view=view) is True
    assert message.edit.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_edit_is_not_recorded_as_rendered():
    # Recording the fingerprint before the edit lands would leave the card permanently stale:
    # every later tick would match the cache and skip, and the change would never go out.
    message = _message()
    message.edit.side_effect = discord.HTTPException(_Response(500), "boom")
    assert await edit_if_changed(message, label="test", embed=_embed()) is False

    message.edit.side_effect = None
    assert await edit_if_changed(message, label="test", embed=_embed()) is True


@pytest.mark.asyncio
async def test_a_deleted_card_is_swallowed():
    # /setup status is what surfaces a missing message. A NotFound must not take the ticker
    # down on every tick from here on.
    message = _message()
    message.edit.side_effect = discord.NotFound(_Response(404), "unknown message")
    assert await edit_if_changed(message, label="test", embed=_embed()) is False


@pytest.mark.asyncio
async def test_a_429_stops_further_edits_for_the_backoff():
    # discord.py only surfaces a 429 after exhausting five internal retries, so by then the
    # bucket is thoroughly saturated and trying again on the next tick is what turned a
    # rate limit into an afternoon-long storm.
    message = _message()
    message.edit.side_effect = discord.HTTPException(_Response(429), "rate limited")
    assert await edit_if_changed(message, label="test", embed=_embed("a")) is False

    message.edit.side_effect = None
    assert await edit_if_changed(message, label="test", embed=_embed("b")) is False
    assert message.edit.await_count == 1


@pytest.mark.asyncio
async def test_editing_resumes_once_the_backoff_expires(monkeypatch):
    message = _message()
    message.edit.side_effect = discord.HTTPException(_Response(429), "rate limited")
    await edit_if_changed(message, label="test", embed=_embed("a"))

    message.edit.side_effect = None
    clock = discord_utils.time.monotonic() + RATE_LIMIT_BACKOFF + 1
    monkeypatch.setattr(discord_utils.time, "monotonic", lambda: clock)
    assert await edit_if_changed(message, label="test", embed=_embed("b")) is True


@pytest.mark.asyncio
async def test_the_cache_is_per_message():
    # Two cards live in the same channel (Now-Playing and history). Keying the guard on
    # anything coarser would let one card's render suppress the other's.
    first, second = _message(1), _message(2)
    await edit_if_changed(first, label="test", embed=_embed())
    assert await edit_if_changed(second, label="test", embed=_embed()) is True
    second.edit.assert_awaited_once()

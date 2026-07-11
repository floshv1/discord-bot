from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.setup.cog import delete_setup_messages, pin, status_line


class _FakeResponse:
    status = 404
    reason = "Not Found"


def _client(channel):
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)
    return client


@pytest.mark.asyncio
async def test_deletes_every_message_from_the_previous_setup():
    message = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)

    deleted = await delete_setup_messages(_client(channel), channel_id=1, message_ids=[10, 11])

    assert deleted == 2
    assert message.delete.await_count == 2


@pytest.mark.asyncio
async def test_ignores_missing_message_ids():
    channel = MagicMock()
    channel.fetch_message = AsyncMock()

    deleted = await delete_setup_messages(_client(channel), channel_id=1, message_ids=[None, None])

    assert deleted == 0
    channel.fetch_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_survives_an_already_deleted_message():
    # An admin may have cleaned the channel by hand — that must not break /setup.
    channel = MagicMock()
    channel.fetch_message = AsyncMock(side_effect=discord.NotFound(_FakeResponse(), "unknown message"))

    deleted = await delete_setup_messages(_client(channel), channel_id=1, message_ids=[10])

    assert deleted == 0


@pytest.mark.asyncio
async def test_no_channel_is_not_an_error():
    client = MagicMock()
    client.get_channel = MagicMock(return_value=None)

    assert await delete_setup_messages(client, channel_id=None, message_ids=[10]) == 0


# --- /setup status ---------------------------------------------------------


def test_status_line_tells_an_unconfigured_feature_what_to_run():
    line = status_line("Currency", "/setup currency", channel_mention=None, missing_messages=0)
    assert "❌" in line
    assert "/setup currency" in line


def test_status_line_shows_the_channel_when_healthy():
    line = status_line("Currency", "/setup currency", channel_mention="#coins", missing_messages=0)
    assert "✅" in line
    assert "#coins" in line


@pytest.mark.asyncio
async def test_setup_messages_are_actually_pinned():
    # Everything calls these "pinned", but nothing ever called .pin() — they just scrolled away.
    message = AsyncMock()
    await pin(message)
    message.pin.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_pin_permission_does_not_break_setup():
    message = AsyncMock()
    message.pin.side_effect = discord.Forbidden(_FakeResponse(), "Missing Permissions")
    await pin(message)  # must not raise


def test_status_line_flags_a_deleted_panel():
    # The exact failure an admin cannot currently see: config row exists, message is gone.
    line = status_line("Currency", "/setup currency", channel_mention="#coins", missing_messages=1)
    assert "⚠️" in line
    assert "/setup currency" in line

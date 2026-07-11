from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.logs.cog import LogsCog, make_embed

GUILD_ID = 1


class _Config:
    def __init__(self, muted, ignored):
        self.guild_id = GUILD_ID
        self.log_channel_id = 123
        self.log_muted_events = set(muted)
        self.log_ignored_channel_ids = set(ignored)


def _cog(muted=(), ignored=()) -> tuple[LogsCog, AsyncMock]:
    log_channel = AsyncMock()
    bot = MagicMock()
    bot.config = _Config(muted, ignored)
    bot.get_channel = MagicMock(return_value=log_channel)
    cog = LogsCog(bot)
    cog._log_to_db = AsyncMock()
    cog._cache_user = AsyncMock()
    return cog, log_channel


def _message(channel_id: int = 55):
    msg = MagicMock()
    msg.author.bot = False
    msg.author.id = 7
    msg.guild.id = GUILD_ID
    msg.channel.id = channel_id
    msg.content = "hello"
    msg.attachments = []
    return msg


def _voice_state(channel_id: int | None):
    state = MagicMock()
    if channel_id is None:
        state.channel = None
    else:
        state.channel = MagicMock()
        state.channel.id = channel_id
    return state


@pytest.mark.asyncio
async def test_send_posts_an_unmuted_event():
    cog, log_channel = _cog()
    await cog._send("member_joined", make_embed(discord.Color.green(), "Member Joined", "x"))
    log_channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_skips_a_muted_event():
    cog, log_channel = _cog(muted={"message_sent"})
    await cog._send("message_sent", make_embed(discord.Color.blue(), "Message Sent", "x"))
    log_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_muted_events_are_still_persisted_to_the_database():
    # The DB half of the audit log is the useful half — muting only silences the channel.
    cog, log_channel = _cog(muted={"message_sent"})

    await cog.on_message(_message())

    log_channel.send.assert_not_awaited()
    cog._log_to_db.assert_awaited_once()
    assert cog._log_to_db.await_args.kwargs["event_type"] == "message_sent"


@pytest.mark.asyncio
async def test_ignored_channel_suppresses_the_message_entirely():
    cog, log_channel = _cog(ignored={55})

    await cog.on_message(_message(channel_id=55))

    log_channel.send.assert_not_awaited()
    cog._log_to_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_events_honour_ignored_channels():
    cog, log_channel = _cog(ignored={99})
    member = MagicMock()
    member.guild.id = GUILD_ID
    member.id = 7

    await cog.on_voice_state_update(member, _voice_state(None), _voice_state(99))

    log_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_self_mute_is_muted_from_the_channel_by_default():
    cog, log_channel = _cog(muted={"voice_muted"})
    member = MagicMock()
    member.guild.id = GUILD_ID
    member.id = 7
    before, after = _voice_state(10), _voice_state(10)
    after.channel = before.channel  # same channel — only the mute flag changed
    before.self_mute, after.self_mute = False, True

    await cog.on_voice_state_update(member, before, after)

    log_channel.send.assert_not_awaited()
    assert cog._log_to_db.await_args.kwargs["event_type"] == "voice_muted"

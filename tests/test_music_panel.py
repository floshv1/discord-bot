import datetime
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.music import panel
from bot.cogs.music.cog import MusicCog
from bot.cogs.music.embeds import (
    EMPTY_HISTORY_TEXT,
    REASON_INACTIVITY,
    build_history_embed,
    build_idle_embed,
    stopped_by,
)
from bot.cogs.music.player import MusicPlayer
from bot.cogs.music.views import NowPlayingView


class _FakeResponse:
    status = 404
    reason = "Not Found"


def _row(title, *, author="Artist", uri="https://y.t/1", requester_id=None, minutes_ago=0):
    played_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago)
    return {
        "track_title": title,
        "track_author": author,
        "track_uri": uri,
        "requester_id": requester_id,
        "played_at": played_at,
    }


# --- the idle card -----------------------------------------------------------


def test_idle_card_says_nothing_is_playing():
    embed = build_idle_embed()
    assert "/play" in embed.description
    # No reason at boot: a restart is not something anyone needs told.
    assert embed.footer.text is None


def test_idle_card_carries_the_reason_playback_ended():
    # This footer replaces the message we used to post into the channel, which is the whole
    # point — the channel stays silent and the card still explains itself.
    embed = build_idle_embed(REASON_INACTIVITY)
    assert embed.footer.text == REASON_INACTIVITY


def test_stopped_by_names_who_pressed_stop():
    assert "Flosh" in stopped_by("Flosh")


def test_idle_view_greys_out_every_button():
    view = NowPlayingView(idle=True)
    assert view.children
    assert all(item.disabled for item in view.children)


def test_idle_view_keeps_its_custom_ids():
    # Disabling is render-only: the persistent view registered at boot still has to be able
    # to dispatch a click that races the redraw.
    view = NowPlayingView(idle=True)
    assert all(item.custom_id.startswith("music:") for item in view.children)
    assert view.timeout is None


# --- the history card --------------------------------------------------------


def test_history_is_newest_first():
    embed = build_history_embed([_row("Newest"), _row("Older", minutes_ago=5)])
    assert embed.description.index("Newest") < embed.description.index("Older")


def test_history_credits_the_requester():
    embed = build_history_embed([_row("Song", requester_id=42)])
    assert "<@42>" in embed.description


def test_history_marks_an_autoplay_pick():
    # requester_id IS NULL is the only thing that distinguishes an autoplay track from a
    # requested one — if it rendered as a member the card would credit the wrong person.
    embed = build_history_embed([_row("Song", requester_id=None)])
    assert "Autoplay" in embed.description
    assert "<@" not in embed.description


def test_history_uses_relative_timestamps():
    # Baked-in text would be a lie an hour later: the card is only redrawn on a track start.
    embed = build_history_embed([_row("Song")])
    assert ":R>" in embed.description


def test_empty_history_says_so():
    assert build_history_embed([]).description == EMPTY_HISTORY_TEXT


def test_history_survives_a_track_with_no_uri():
    embed = build_history_embed([_row("Song", uri=None)])
    assert "Song" in embed.description


# --- redraw plumbing ---------------------------------------------------------


def _bot_with_channel():
    message = AsyncMock(spec=discord.PartialMessage)
    message.id = 999
    channel = MagicMock(spec=discord.TextChannel)
    channel.get_partial_message = MagicMock(return_value=message)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    return bot, message


@pytest.mark.asyncio
async def test_redraw_reports_false_when_the_guild_never_ran_setup():
    # False is what makes the caller fall back to the old transient card.
    with patch.object(panel.service, "get_config", AsyncMock(return_value=None)):
        assert await panel.redraw_now_playing(MagicMock(), 1, None) is False


@pytest.mark.asyncio
async def test_redraw_reports_false_when_the_database_is_unreachable():
    # Losing the config must cost us the pinned card, not the Now-Playing card altogether.
    with patch.object(panel.service, "get_config", AsyncMock(side_effect=RuntimeError("no pool"))):
        assert await panel.redraw_now_playing(MagicMock(), 1, None) is False


@pytest.mark.asyncio
async def test_redraw_edits_the_pinned_message_in_place():
    bot, message = _bot_with_channel()
    config = {"channel_id": 5, "now_playing_message_id": 999, "history_message_id": 998}
    with patch.object(panel.service, "get_config", AsyncMock(return_value=config)):
        assert await panel.redraw_now_playing(bot, 1, None) is True
    message.edit.assert_awaited_once()
    # Never .send(): a permanent card is edited, never re-posted.
    assert isinstance(message.edit.await_args.kwargs["view"], NowPlayingView)


@pytest.mark.asyncio
async def test_redraw_of_a_hand_deleted_card_is_swallowed():
    # /setup status is what surfaces a missing message; a NotFound here must not take the
    # ticker down on every tick from now on.
    bot, message = _bot_with_channel()
    message.edit.side_effect = discord.NotFound(_FakeResponse(), "unknown message")
    config = {"channel_id": 5, "now_playing_message_id": 999, "history_message_id": 998}
    with patch.object(panel.service, "get_config", AsyncMock(return_value=config)):
        assert await panel.redraw_now_playing(bot, 1, None) is True  # must not raise


# --- the ticker --------------------------------------------------------------


def _cog() -> MusicCog:
    return MusicCog(MagicMock())


def _playing_guild(guild_id=1):
    player = MagicMock(spec=MusicPlayer)
    player.current = MagicMock()
    guild = MagicMock()
    guild.id = guild_id
    guild.voice_client = player
    return guild, player


@pytest.mark.asyncio
async def test_history_dirty_flag_is_drained_swap_then_iterate():
    # A track starting mid-redraw must dirty the flag again, not be swallowed by the
    # refresh already in flight.
    cog = _cog()
    cog.bot.guilds = []
    cog._history_dirty = {1}

    async def redraw_and_race(_bot, guild_id):
        cog._history_dirty.add(guild_id)
        return True

    with patch.object(panel, "redraw_history", redraw_and_race):
        await cog.panel_refresh_ticker()

    assert cog._history_dirty == {1}


@pytest.mark.asyncio
async def test_ticker_redraws_a_playing_guild():
    cog = _cog()
    guild, player = _playing_guild()
    cog.bot.guilds = [guild]

    redraw = AsyncMock(return_value=True)
    with patch.object(panel, "redraw_now_playing", redraw):
        await cog.panel_refresh_ticker()

    redraw.assert_awaited_once_with(cog.bot, 1, player)
    assert 1 in cog._card_playing


@pytest.mark.asyncio
async def test_ticker_flips_the_card_back_to_idle_when_the_player_vanishes():
    # The player can disappear through a path we don't hook — dragged out of the channel,
    # a node that dropped. Without this the card advertises a song that ended an hour ago.
    cog = _cog()
    guild = MagicMock()
    guild.id = 1
    guild.voice_client = None
    cog.bot.guilds = [guild]
    cog._card_playing = {1}
    cog._idle_reason = {1: REASON_INACTIVITY}

    redraw = AsyncMock(return_value=True)
    with patch.object(panel, "redraw_now_playing", redraw):
        await cog.panel_refresh_ticker()

    redraw.assert_awaited_once_with(cog.bot, 1, None, REASON_INACTIVITY)
    assert cog._card_playing == set()
    assert cog._idle_reason == {}


@pytest.mark.asyncio
async def test_ticker_leaves_an_idle_guild_alone():
    # An idle card must not be re-edited every 5 seconds forever.
    cog = _cog()
    guild = MagicMock()
    guild.id = 1
    guild.voice_client = None
    cog.bot.guilds = [guild]

    redraw = AsyncMock(return_value=True)
    with patch.object(panel, "redraw_now_playing", redraw):
        await cog.panel_refresh_ticker()

    redraw.assert_not_awaited()


# --- the unconfigured fallback ----------------------------------------------


def _fallback_player():
    player = MagicMock()
    player.__class__ = MusicPlayer
    player.played_ids = set()
    player.recent_tracks = deque(maxlen=10)
    player.autoplay_enabled = False
    player.now_playing_message = None
    player.text_channel = AsyncMock()
    player.guild.id = 1
    player.position = 0
    player.queue = []
    return player


def _playable_track():
    track = MagicMock()
    track.identifier = "abc"
    track.title = "Song"
    track.author = "Artist"
    track.uri = "https://y.t/1"
    track.length = 180_000
    track.artwork = None
    track.album = None
    track.extras.requester = 7
    return track


@pytest.mark.asyncio
async def test_unconfigured_guild_still_gets_a_transient_card():
    # The regression that would silently break every guild that hasn't run /setup music.
    cog = _cog()
    player = _fallback_player()
    payload = MagicMock(player=player, track=_playable_track())

    with patch.object(panel, "redraw_now_playing", AsyncMock(return_value=False)):
        await cog.on_wavelink_track_start(payload)

    player.text_channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_configured_guild_posts_nothing_transient():
    cog = _cog()
    player = _fallback_player()
    payload = MagicMock(player=player, track=_playable_track())

    with patch.object(panel, "redraw_now_playing", AsyncMock(return_value=True)):
        await cog.on_wavelink_track_start(payload)

    player.text_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_end_of_playback_says_it_out_loud_only_when_there_is_no_card():
    cog = _cog()
    player = _fallback_player()

    with patch.object(panel, "redraw_now_playing", AsyncMock(return_value=True)):
        await cog._end_playback(player, REASON_INACTIVITY)
    player.text_channel.send.assert_not_awaited()

    with patch.object(panel, "redraw_now_playing", AsyncMock(return_value=False)):
        await cog._end_playback(player, REASON_INACTIVITY)
    player.text_channel.send.assert_awaited_once_with(REASON_INACTIVITY)

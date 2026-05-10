from unittest.mock import MagicMock

import discord

from bot.cogs.music.cog import _fmt_ms
from bot.cogs.music.views import QueueView


def test_fmt_ms_seconds():
    assert _fmt_ms(5_000) == "0:05"


def test_fmt_ms_minutes():
    assert _fmt_ms(185_000) == "3:05"


def test_fmt_ms_hours():
    assert _fmt_ms(3_661_000) == "1:01:01"


def _make_track(title: str, length_ms: int, uri: str = "https://youtube.com", requester_id: int | None = None):
    track = MagicMock()
    track.title = title
    track.length = length_ms
    track.uri = uri
    track.author = "Artist"
    track.artwork = None
    track.extras = MagicMock()
    track.extras.requester = requester_id
    return track


def test_build_queue_embed_empty():
    player = MagicMock()
    player.current = None
    player.queue.is_empty = True
    player.queue.count = 0
    player.queue.__iter__ = lambda self: iter([])
    embed = QueueView(player).build_embed()
    assert isinstance(embed, discord.Embed)
    assert "empty" in embed.description.lower()


def test_build_queue_embed_with_tracks():
    player = MagicMock()
    player.current = _make_track("Song A", 180_000)
    track_b = _make_track("Song B", 120_000, requester_id=123)
    player.queue.is_empty = False
    player.queue.count = 1
    player.queue.__iter__ = lambda self: iter([track_b])
    embed = QueueView(player).build_embed()
    assert "Song A" in embed.description
    assert "Song B" in embed.description
    assert "<@123>" in embed.description

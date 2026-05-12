from collections import deque
from unittest.mock import MagicMock

import discord

from bot.cogs.music.cog import _fmt_ms
from bot.cogs.music.player import MusicPlayer
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


# --- session history recording ---


def _make_music_player_mock(*, autoplay_enabled: bool = False) -> MagicMock:
    player = MagicMock()
    player.played_ids = set()
    player.recent_tracks = deque(maxlen=10)
    player.autoplay_enabled = autoplay_enabled
    player.text_channel = None
    player.now_playing_message = None
    return player


async def test_track_start_records_in_history():
    from bot.cogs.music.cog import MusicCog

    cog = object.__new__(MusicCog)
    player = _make_music_player_mock()
    player.__class__ = MusicPlayer

    track = MagicMock()
    track.identifier = "abc123"
    track.extras = MagicMock()
    track.extras.requester = None

    payload = MagicMock()
    payload.player = player
    payload.track = track

    await cog.on_wavelink_track_start(payload)

    assert "abc123" in player.played_ids
    assert track in list(player.recent_tracks)


def test_build_queue_embed_empty():
    player = MagicMock()
    player.current = None
    player.queue.is_empty = True
    player.queue.count = 0
    player.queue.__iter__ = lambda self: iter([])
    embed = QueueView(player).build_embed()
    assert isinstance(embed, discord.Embed)
    assert "empty" in embed.description.lower()


# --- MusicPlayer session-state fields ---


def _make_player() -> MusicPlayer:
    player = object.__new__(MusicPlayer)
    player.played_ids = set()
    player.recent_tracks = deque(maxlen=10)
    return player


def test_player_played_ids_starts_empty():
    player = _make_player()
    assert player.played_ids == set()


def test_player_recent_tracks_maxlen():
    player = _make_player()
    for i in range(15):
        player.recent_tracks.append(f"track_{i}")
    assert len(player.recent_tracks) == 10
    assert list(player.recent_tracks)[0] == "track_5"


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

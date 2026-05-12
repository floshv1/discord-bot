from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

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
    player.seed_pattern_index = 0
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


# --- on_wavelink_track_end autoplay algorithm ---


async def test_autoplay_uses_top_candidate_not_random():
    from bot.cogs.music.cog import MusicCog

    cog = object.__new__(MusicCog)
    player = _make_music_player_mock(autoplay_enabled=True)
    player.__class__ = MusicPlayer
    player.connected = True
    player.queue = MagicMock()
    player.queue.is_empty = True
    player.queue.put_wait = AsyncMock()
    player.play = AsyncMock()
    player.queue.get = MagicMock(return_value=MagicMock())

    seed_track = _make_track_with_author("Daft Punk", "Get Lucky")
    seed_track.identifier = "seed_id"
    player.recent_tracks.append(seed_track)

    result_a = MagicMock()
    result_a.identifier = "a"
    result_b = MagicMock()
    result_b.identifier = "b"

    payload = MagicMock()
    payload.player = player
    payload.track = seed_track

    with patch("bot.cogs.music.cog.wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [result_a, result_b]
        await cog.on_wavelink_track_end(payload)

    player.queue.put_wait.assert_awaited_once_with(result_a)


async def test_autoplay_skips_already_played_tracks():
    from bot.cogs.music.cog import MusicCog

    cog = object.__new__(MusicCog)
    player = _make_music_player_mock(autoplay_enabled=True)
    player.__class__ = MusicPlayer
    player.connected = True
    player.queue = MagicMock()
    player.queue.is_empty = True
    player.queue.put_wait = AsyncMock()
    player.play = AsyncMock()
    player.queue.get = MagicMock(return_value=MagicMock())

    seed_track = _make_track_with_author("Daft Punk", "Get Lucky")
    seed_track.identifier = "seed_id"
    player.recent_tracks.append(seed_track)
    player.played_ids = {"already"}

    already_played = MagicMock()
    already_played.identifier = "already"
    fresh = MagicMock()
    fresh.identifier = "fresh"

    payload = MagicMock()
    payload.player = player
    payload.track = seed_track

    with patch("bot.cogs.music.cog.wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [already_played, fresh]
        await cog.on_wavelink_track_end(payload)

    player.queue.put_wait.assert_awaited_once_with(fresh)


async def test_autoplay_increments_seed_pattern_index():
    from bot.cogs.music.cog import MusicCog

    cog = object.__new__(MusicCog)
    player = _make_music_player_mock(autoplay_enabled=True)
    player.__class__ = MusicPlayer
    player.connected = True
    player.queue = MagicMock()
    player.queue.is_empty = True
    player.queue.put_wait = AsyncMock()
    player.play = AsyncMock()
    player.queue.get = MagicMock(return_value=MagicMock())

    seed_track = _make_track_with_author("Daft Punk", "Get Lucky")
    seed_track.identifier = "seed_id"
    player.recent_tracks.append(seed_track)

    result = MagicMock()
    result.identifier = "r"

    payload = MagicMock()
    payload.player = player
    payload.track = seed_track

    with patch("bot.cogs.music.cog.wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [result]
        await cog.on_wavelink_track_end(payload)

    assert player.seed_pattern_index == 1


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
    player.seed_pattern_index = 0
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


def test_player_seed_pattern_index_starts_zero():
    player = _make_player()
    assert player.seed_pattern_index == 0


# --- _build_autoplay_query ---


def _make_track_with_author(author: str, title: str = "Title") -> MagicMock:
    t = MagicMock()
    t.author = author
    t.title = title
    return t


def test_build_autoplay_query_pattern_0_uses_artist():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 0) == "Daft Punk"


def test_build_autoplay_query_pattern_1_uses_songs_like():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 1) == "songs like Get Lucky"


def test_build_autoplay_query_pattern_2_uses_mix():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 2) == "Daft Punk mix"


def test_build_autoplay_query_pattern_3_uses_similar_to():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 3) == "music similar to Daft Punk"


def test_build_autoplay_query_pattern_4_uses_playlist():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 4) == "Get Lucky playlist"


def test_build_autoplay_query_wraps_at_5():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 5) == _build_autoplay_query(tracks, 0)
    assert _build_autoplay_query(tracks, 6) == _build_autoplay_query(tracks, 1)


def test_build_autoplay_query_uses_most_common_artist():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [
        _make_track_with_author("Daft Punk", "One More Time"),
        _make_track_with_author("The Weeknd", "Blinding Lights"),
        _make_track_with_author("Daft Punk", "Get Lucky"),
    ]
    assert _build_autoplay_query(tracks, 0) == "Daft Punk"


def test_build_autoplay_query_tie_uses_either_artist():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [
        _make_track_with_author("Daft Punk", "One More Time"),
        _make_track_with_author("The Weeknd", "Blinding Lights"),
    ]
    result = _build_autoplay_query(tracks, 0)
    assert result in ("Daft Punk", "The Weeknd")


def test_build_autoplay_query_title_comes_from_last_track():
    from bot.cogs.music.cog import _build_autoplay_query

    tracks = [
        _make_track_with_author("Daft Punk", "One More Time"),
        _make_track_with_author("Daft Punk", "Get Lucky"),
    ]
    assert _build_autoplay_query(tracks, 1) == "songs like Get Lucky"


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

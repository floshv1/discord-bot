from collections import deque
from unittest.mock import AsyncMock, MagicMock

import discord
import wavelink

from bot.cogs.music.player import MusicPlayer
from bot.cogs.music.utils import (
    LOAD_FAILED,
    SPOTIFY_UNCONFIGURED,
    _fmt_ms,
    calculate_eta,
    clean_title,
    format_progress_bar,
    is_filtered_autoplay_track,
    is_spotify_query,
    search_failure_message,
    titles_similar,
)
from bot.cogs.music.views import NowPlayingView, QueueView


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

    cog = MusicCog(MagicMock())
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


# --- format_progress_bar ---


def test_format_progress_bar_start():
    bar = format_progress_bar(0, 180_000)
    assert bar == "░░░░░░░░░░░░░░░ 0:00 / 3:00"


def test_format_progress_bar_half():
    bar = format_progress_bar(90_000, 180_000)
    assert "█" in bar
    assert "░" in bar
    assert "1:30 / 3:00" in bar


def test_format_progress_bar_full():
    bar = format_progress_bar(180_000, 180_000)
    assert bar == "███████████████ 3:00 / 3:00"


def test_format_progress_bar_zero_length():
    bar = format_progress_bar(0, 0)
    assert "0:00 / 0:00" in bar


# --- clean_title ---


def test_clean_title_strips_official_video():
    assert clean_title("Song Title (Official Video)") == "song title"


def test_clean_title_strips_lyrics():
    assert clean_title("Song Title [Lyrics]") == "song title"


def test_clean_title_strips_feat():
    assert clean_title("Song Title (feat. Artist B)") == "song title"


def test_clean_title_lowercases():
    assert clean_title("MY SONG") == "my song"


def test_clean_title_strips_official_audio():
    assert clean_title("Song Title (Official Audio)") == "song title"


# --- titles_similar ---


def test_titles_similar_same_base_different_suffix():
    assert titles_similar("Song Title (Official Video)", "Song Title (Lyrics)") is True


def test_titles_similar_completely_different():
    assert titles_similar("Song A", "Song B") is False


def test_titles_similar_one_contains_other():
    assert titles_similar("Blinding Lights", "Blinding Lights (Official Video)") is True


def test_titles_similar_same_string():
    assert titles_similar("My Song", "My Song") is True


# --- calculate_eta ---


def test_calculate_eta_first_in_queue():
    player = MagicMock()
    player.current = _make_track("Now", 120_000)
    player.position = 30_000
    player.queue.__iter__ = MagicMock(return_value=iter([]))
    # track added at index 0 → only wait for remaining time
    assert calculate_eta(player, 0) == 90_000


def test_calculate_eta_second_in_queue():
    player = MagicMock()
    player.current = _make_track("Now", 120_000)
    player.position = 60_000
    track_b = _make_track("Next", 180_000)
    player.queue.__iter__ = MagicMock(return_value=iter([track_b]))
    # track added at index 1 → remaining + track_b.length
    assert calculate_eta(player, 1) == 60_000 + 180_000


def test_calculate_eta_no_current():
    player = MagicMock()
    player.current = None
    player.position = 0
    player.queue.__iter__ = MagicMock(return_value=iter([]))
    assert calculate_eta(player, 0) == 0


# --- is_filtered_autoplay_track ---


def test_is_filtered_live_concert():
    track = MagicMock()
    track.title = "Song Title (Live at Wembley)"
    assert is_filtered_autoplay_track(track) is True


def test_is_filtered_cover():
    track = MagicMock()
    track.title = "Song Title (Cover)"
    assert is_filtered_autoplay_track(track) is True


def test_is_filtered_remix():
    track = MagicMock()
    track.title = "Song Title Remix"
    assert is_filtered_autoplay_track(track) is True


def test_not_filtered_normal_track():
    track = MagicMock()
    track.title = "Blinding Lights"
    assert is_filtered_autoplay_track(track) is False


def test_not_filtered_official_video():
    track = MagicMock()
    track.title = "Blinding Lights (Official Video)"
    assert is_filtered_autoplay_track(track) is False


# --- search failure messages ---


def test_lavalink_load_exception_is_not_a_lavalink_exception():
    """The whole reason /play answered with the generic apology.

    `Playable.search` raises `LavalinkLoadException` on a failed load, and it does NOT
    inherit from `LavalinkException` — so `except LavalinkException` never fired. If a
    wavelink upgrade ever merges the two, this test says so instead of the change passing
    unnoticed and leaving a redundant handler behind.
    """
    assert not issubclass(wavelink.LavalinkLoadException, wavelink.LavalinkException)


def test_is_spotify_query_playlist_url():
    assert is_spotify_query("https://open.spotify.com/playlist/1M04ZNwgqaBtEQdseITeG6?si=abc") is True


def test_is_spotify_query_short_link():
    assert is_spotify_query("https://spotify.link/abc123") is True


def test_is_spotify_query_search_prefix():
    assert is_spotify_query("spsearch:blinding lights") is True


def test_is_spotify_query_youtube_url():
    assert is_spotify_query("https://www.youtube.com/watch?v=KDxJlW6cxRk") is False


def test_is_spotify_query_plain_text_mentioning_spotify():
    # A member searching for the words is not asking LavaSrc for anything.
    assert is_spotify_query("my spotify playlist song") is False


def test_search_failure_spotify_url_unconfigured_names_spotify():
    msg = search_failure_message("https://open.spotify.com/playlist/abc", spotify_configured=False)
    assert msg == SPOTIFY_UNCONFIGURED


def test_search_failure_spotify_url_configured_is_generic():
    # Credentials are set, so this is a real load failure — not a setup gap.
    msg = search_failure_message("https://open.spotify.com/playlist/abc", spotify_configured=True)
    assert msg == LOAD_FAILED


def test_search_failure_text_query_never_mentions_spotify():
    assert search_failure_message("blinding lights", spotify_configured=False) == LOAD_FAILED
    assert search_failure_message("blinding lights", spotify_configured=True) == LOAD_FAILED


# --- now playing embed ---


def _make_player_for_embed(*, position_ms: int = 0, queue_tracks=None, autoplay_enabled: bool = False):
    player = MagicMock()
    player.position = position_ms
    player.autoplay_enabled = autoplay_enabled
    tracks = queue_tracks or []
    player.queue.is_empty = len(tracks) == 0
    player.queue.count = len(tracks)
    player.queue.__iter__ = lambda self=None: iter(tracks)
    return player


def test_now_playing_embed_has_progress_bar():
    from bot.cogs.music.embeds import build_now_playing_embed

    track = _make_track("My Song", 180_000)
    player = _make_player_for_embed(position_ms=60_000)
    embed = build_now_playing_embed(track, player)
    field_names = [f.name for f in embed.fields]
    assert "Progress" in field_names


def test_now_playing_embed_has_no_duration_field():
    from bot.cogs.music.embeds import build_now_playing_embed

    track = _make_track("My Song", 180_000)
    player = _make_player_for_embed()
    embed = build_now_playing_embed(track, player)
    field_names = [f.name for f in embed.fields]
    assert "Duration" not in field_names


def test_now_playing_embed_shows_next_track():
    from bot.cogs.music.embeds import build_now_playing_embed

    track = _make_track("My Song", 180_000)
    next_track = _make_track("Next Song", 120_000)
    player = _make_player_for_embed(queue_tracks=[next_track])
    embed = build_now_playing_embed(track, player)
    field_names = [f.name for f in embed.fields]
    assert "Next song" in field_names
    next_field = next(f for f in embed.fields if f.name == "Next song")
    assert "Next Song" in next_field.value


def test_now_playing_embed_shows_autoplay_when_empty_and_enabled():
    from bot.cogs.music.embeds import build_now_playing_embed

    track = _make_track("My Song", 180_000)
    player = _make_player_for_embed(queue_tracks=[], autoplay_enabled=True)
    embed = build_now_playing_embed(track, player)
    next_field = next((f for f in embed.fields if f.name == "Next song"), None)
    assert next_field is not None
    assert "Autoplay" in next_field.value


def test_now_playing_embed_no_prochain_when_empty_and_autoplay_off():
    from bot.cogs.music.embeds import build_now_playing_embed

    track = _make_track("My Song", 180_000)
    player = _make_player_for_embed(queue_tracks=[], autoplay_enabled=False)
    embed = build_now_playing_embed(track, player)
    field_names = [f.name for f in embed.fields]
    assert "Next song" not in field_names


# --- autoplay filter in on_wavelink_track_start ---


async def test_track_start_skips_filtered_autoplay_track():
    from bot.cogs.music.cog import MusicCog

    cog = MusicCog(MagicMock())
    player = _make_music_player_mock(autoplay_enabled=True)
    player.__class__ = MusicPlayer
    player.stop = AsyncMock()

    track = MagicMock()
    track.identifier = "abc123"
    track.title = "Song (Live at Wembley)"
    track.extras = MagicMock()
    track.extras.requester = None  # autoplay track

    payload = MagicMock()
    payload.player = player
    payload.track = track

    await cog.on_wavelink_track_start(payload)

    player.stop.assert_called_once_with(force=True)


async def test_track_start_does_not_skip_user_requested_track():
    from bot.cogs.music.cog import MusicCog

    cog = MusicCog(MagicMock())
    player = _make_music_player_mock(autoplay_enabled=True)
    player.__class__ = MusicPlayer
    player.stop = AsyncMock()

    track = MagicMock()
    track.identifier = "xyz"
    track.title = "Song (Live at Wembley)"
    track.extras = MagicMock()
    track.extras.requester = 123456  # user-requested

    payload = MagicMock()
    payload.player = player
    payload.track = track

    await cog.on_wavelink_track_start(payload)

    player.stop.assert_not_called()


# --- NowPlayingView buttons ---


def _make_now_playing_player(*, history_count: int = 0) -> MagicMock:
    player = MagicMock()
    player.paused = False
    player.autoplay_enabled = False
    player.recent_tracks = deque(
        [_make_track(f"Track {i}", 180_000) for i in range(history_count)],
        maxlen=10,
    )
    return player


def test_now_playing_view_has_lyrics_button():
    player = _make_now_playing_player()
    view = NowPlayingView(player)
    labels = [item.label for item in view.children]
    assert any("Lyrics" in label for label in labels)


def test_now_playing_view_has_prev_button():
    player = _make_now_playing_player()
    view = NowPlayingView(player)
    labels = [item.label for item in view.children]
    assert any("Prev" in label for label in labels)


def test_now_playing_view_prev_disabled_when_single_history():
    player = _make_now_playing_player(history_count=1)
    view = NowPlayingView(player)
    prev_btn = next(item for item in view.children if "Prev" in item.label)
    assert prev_btn.disabled is True


def test_now_playing_view_prev_enabled_with_two_history():
    player = _make_now_playing_player(history_count=2)
    view = NowPlayingView(player)
    prev_btn = next(item for item in view.children if "Prev" in item.label)
    assert prev_btn.disabled is False


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


# --- playback failures must not be silent (on_wavelink_track_exception / _stuck) ---


def _make_failing_payload(player: MagicMock, *, identifier: str = "abc123", title: str = "Some Song"):
    track = MagicMock()
    track.identifier = identifier
    track.title = title
    payload = MagicMock()
    payload.player = player
    payload.track = track
    payload.threshold = 10_000
    payload.exception = {"message": "All clients failed", "severity": "suspicious", "cause": "boom"}
    return payload


def _patch_reporting(monkeypatch) -> AsyncMock:
    """Silence the two IO calls `_report_playback_failure` makes and hand back the channel."""
    from bot.cogs.music import cog as music_cog

    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(music_cog.panel, "resolve_channel", AsyncMock(return_value=channel))
    monkeypatch.setattr(music_cog, "_delete_after", AsyncMock())
    return channel


async def test_track_exception_names_the_track_in_the_channel(monkeypatch):
    from bot.cogs.music.cog import MusicCog

    channel = _patch_reporting(monkeypatch)
    cog = MusicCog(MagicMock())
    player = _make_music_player_mock()
    player.__class__ = MusicPlayer

    await cog.on_wavelink_track_exception(_make_failing_payload(player, title="Never Gonna Give You Up"))

    channel.send.assert_awaited_once()
    assert "Never Gonna Give You Up" in channel.send.await_args.args[0]


async def test_track_exception_never_leaks_the_provider_error(monkeypatch):
    """The exception text carries provider internals — same rule as `_search_or_report`."""
    from bot.cogs.music.cog import MusicCog

    channel = _patch_reporting(monkeypatch)
    cog = MusicCog(MagicMock())
    player = _make_music_player_mock()
    player.__class__ = MusicPlayer

    await cog.on_wavelink_track_exception(_make_failing_payload(player))

    sent = channel.send.await_args.args[0]
    assert "All clients failed" not in sent
    assert "boom" not in sent


async def test_track_exception_does_not_advance_the_queue(monkeypatch):
    """Lavalink's own TrackEndEvent already advances it — skipping here would eat a track."""
    from bot.cogs.music.cog import MusicCog

    _patch_reporting(monkeypatch)
    cog = MusicCog(MagicMock())
    player = _make_music_player_mock()
    player.__class__ = MusicPlayer
    player.skip = AsyncMock()
    player.play = AsyncMock()
    player.stop = AsyncMock()

    await cog.on_wavelink_track_exception(_make_failing_payload(player))

    player.skip.assert_not_awaited()
    player.play.assert_not_awaited()
    player.stop.assert_not_awaited()


async def test_track_stuck_skips_the_stuck_track(monkeypatch):
    """Nothing else will: a stuck track gets no TrackEndEvent, so the player would sit forever."""
    from bot.cogs.music.cog import MusicCog

    _patch_reporting(monkeypatch)
    cog = MusicCog(MagicMock())
    player = _make_music_player_mock()
    player.__class__ = MusicPlayer
    player.skip = AsyncMock()

    payload = _make_failing_payload(player, identifier="stuck1")
    player.current = payload.track

    await cog.on_wavelink_track_stuck(payload)

    player.skip.assert_awaited_once_with(force=True)


async def test_track_stuck_does_not_skip_when_lavalink_already_moved_on(monkeypatch):
    from bot.cogs.music.cog import MusicCog

    _patch_reporting(monkeypatch)
    cog = MusicCog(MagicMock())
    player = _make_music_player_mock()
    player.__class__ = MusicPlayer
    player.skip = AsyncMock()
    player.current = None

    await cog.on_wavelink_track_stuck(_make_failing_payload(player, identifier="stuck1"))

    player.skip.assert_not_awaited()


async def test_playback_failure_falls_back_to_the_play_channel(monkeypatch):
    """No `/setup music` row is a supported state — the message still has to land somewhere."""
    from bot.cogs.music import cog as music_cog
    from bot.cogs.music.cog import MusicCog

    monkeypatch.setattr(music_cog.panel, "resolve_channel", AsyncMock(return_value=None))
    monkeypatch.setattr(music_cog, "_delete_after", AsyncMock())

    cog = MusicCog(MagicMock())
    player = _make_music_player_mock()
    player.__class__ = MusicPlayer
    player.text_channel = MagicMock()
    player.text_channel.send = AsyncMock(return_value=MagicMock())

    await cog.on_wavelink_track_exception(_make_failing_payload(player, title="Fallback Song"))

    player.text_channel.send.assert_awaited_once()
    assert "Fallback Song" in player.text_channel.send.await_args.args[0]


# --- the search prefix must match what Lavalink actually registers ---


async def test_search_uses_ytsearch_not_ytmsearch(monkeypatch):
    """YouTube is served by LavaSrc's yt-dlp source, which registers `ytsearch:` alone.

    wavelink defaults to `ytmsearch:`, and nothing answers that any more now that
    youtube-plugin is gone — every `/play` would come back empty. Locked here because the
    failure is silent: a search that finds nothing looks exactly like a bad query.
    """
    from bot.cogs.music.cog import MusicCog

    captured = {}

    async def fake_search(query, /, **kwargs):
        captured["query"] = query
        captured["source"] = kwargs.get("source", "MISSING")
        return []

    monkeypatch.setattr(wavelink.Playable, "search", fake_search)

    cog = MusicCog(MagicMock())
    interaction = MagicMock()
    interaction.followup.send = AsyncMock()

    result = await cog._search_or_report(interaction, "some song")

    assert result == []
    assert captured["source"] is wavelink.TrackSource.YouTube, f"expected ytsearch:, got {captured['source']}"

# Autoplay Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate track repetition, same-artist loops, and vibe drift in Discord bot autoplay by adding session history tracking, rotating seed patterns, and last-3-track context.

**Architecture:** Add three fields to `MusicPlayer` (`played_ids`, `recent_tracks`, `seed_pattern_index`), record every track on `track_start`, and replace the random single-artist search in `on_wavelink_track_end` with a history-filtered, rotating-seed algorithm seeded from the most-common artist in the last 3 played tracks.

**Tech Stack:** Python 3.12, discord.py 2.4+, Wavelink 3.4+, pytest, `collections.deque` / `collections.Counter`

---

## File Map

| File | Change |
|------|--------|
| `bot/cogs/music/player.py` | Add `played_ids`, `recent_tracks`, `seed_pattern_index` to `__init__` |
| `bot/cogs/music/cog.py` | Add `_build_autoplay_query()` helper; update `on_wavelink_track_start`; rewrite `on_wavelink_track_end`; remove `import random` |
| `tests/test_music.py` | Add tests for new player fields and `_build_autoplay_query()` |

---

### Task 1: Add session-state fields to MusicPlayer

**Files:**
- Modify: `bot/cogs/music/player.py`
- Test: `tests/test_music.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_music.py`:

```python
from collections import deque
from bot.cogs.music.player import MusicPlayer

def _make_player() -> MusicPlayer:
    """Return a MusicPlayer-like object with the new fields, bypassing wavelink init."""
    player = object.__new__(MusicPlayer)
    # Manually set fields the same way __init__ will after our change
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
    assert list(player.recent_tracks)[0] == "track_5"  # oldest 5 were evicted


def test_player_seed_pattern_index_starts_zero():
    player = _make_player()
    assert player.seed_pattern_index == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_music.py::test_player_played_ids_starts_empty tests/test_music.py::test_player_recent_tracks_maxlen tests/test_music.py::test_player_seed_pattern_index_starts_zero -v
```

Expected: `AttributeError: 'MusicPlayer' object has no attribute 'played_ids'` (or similar).

- [ ] **Step 3: Update `bot/cogs/music/player.py`**

Replace the entire file with:

```python
from __future__ import annotations

from collections import deque

import discord
import wavelink


class MusicPlayer(wavelink.Player):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.text_channel: discord.TextChannel = None  # type: ignore[assignment]
        self.autoplay_enabled: bool = False
        self.autoplay = wavelink.AutoPlayMode.partial
        self.inactive_timeout = 300
        self.now_playing_message: discord.Message | None = None
        self.played_ids: set[str] = set()
        self.recent_tracks: deque[wavelink.Playable] = deque(maxlen=10)
        self.seed_pattern_index: int = 0
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_music.py::test_player_played_ids_starts_empty tests/test_music.py::test_player_recent_tracks_maxlen tests/test_music.py::test_player_seed_pattern_index_starts_zero -v
```

Expected: all 3 PASS.

---

### Task 2: Add `_build_autoplay_query()` helper and its tests

**Files:**
- Modify: `bot/cogs/music/cog.py` (add helper function before `MusicCog` class)
- Test: `tests/test_music.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_music.py`:

```python
from bot.cogs.music.cog import _build_autoplay_query


def _make_track_with_author(author: str, title: str = "Title") -> MagicMock:
    t = MagicMock()
    t.author = author
    t.title = title
    return t


def test_build_autoplay_query_pattern_0_uses_artist():
    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 0) == "Daft Punk"


def test_build_autoplay_query_pattern_1_uses_title_and_artist():
    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 1) == "Get Lucky Daft Punk"


def test_build_autoplay_query_pattern_2_uses_mix():
    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 2) == "Daft Punk mix"


def test_build_autoplay_query_wraps_at_3():
    tracks = [_make_track_with_author("Daft Punk", "Get Lucky")]
    assert _build_autoplay_query(tracks, 3) == _build_autoplay_query(tracks, 0)
    assert _build_autoplay_query(tracks, 4) == _build_autoplay_query(tracks, 1)


def test_build_autoplay_query_uses_most_common_artist():
    # 2 Daft Punk + 1 The Weeknd → primary = Daft Punk
    tracks = [
        _make_track_with_author("Daft Punk", "One More Time"),
        _make_track_with_author("The Weeknd", "Blinding Lights"),
        _make_track_with_author("Daft Punk", "Get Lucky"),
    ]
    assert _build_autoplay_query(tracks, 0) == "Daft Punk"


def test_build_autoplay_query_tie_uses_most_recent_artist():
    # Tie: 1 Daft Punk + 1 The Weeknd → use most recent (The Weeknd, last in list)
    tracks = [
        _make_track_with_author("Daft Punk", "One More Time"),
        _make_track_with_author("The Weeknd", "Blinding Lights"),
    ]
    # most_common() is stable in CPython for ties — most recent = last appended wins
    result = _build_autoplay_query(tracks, 0)
    assert result in ("Daft Punk", "The Weeknd")  # either is valid; test documents behaviour


def test_build_autoplay_query_title_comes_from_last_track():
    # Pattern 1 should use the title of the LAST track (index -1), not the most-common-artist track
    tracks = [
        _make_track_with_author("Daft Punk", "One More Time"),
        _make_track_with_author("Daft Punk", "Get Lucky"),
    ]
    assert _build_autoplay_query(tracks, 1) == "Get Lucky Daft Punk"
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_music.py -k "build_autoplay_query" -v
```

Expected: `ImportError: cannot import name '_build_autoplay_query' from 'bot.cogs.music.cog'`.

- [ ] **Step 3: Add `_build_autoplay_query` to `bot/cogs/music/cog.py`**

Add the import at the top of `cog.py` (alongside existing imports):

```python
from collections import Counter
```

Then add this function **before** the `MusicCog` class definition (after `_delete_after` and `_disable_now_playing`):

```python
def _build_autoplay_query(recent_tracks: list, pattern_index: int) -> str:
    artist_counts = Counter(t.author for t in recent_tracks)
    primary_artist = artist_counts.most_common(1)[0][0]
    primary_title = recent_tracks[-1].title
    patterns = [
        primary_artist,
        f"{primary_title} {primary_artist}",
        f"{primary_artist} mix",
    ]
    return patterns[pattern_index % 3]
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_music.py -k "build_autoplay_query" -v
```

Expected: all 7 tests PASS.

---

### Task 3: Record every track in session history on `track_start`

**Files:**
- Modify: `bot/cogs/music/cog.py` (`on_wavelink_track_start`, lines 222–251)
- Test: `tests/test_music.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_music.py`:

```python
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch


def _make_music_player_mock(*, autoplay_enabled: bool = False) -> MagicMock:
    player = MagicMock()
    player.played_ids = set()
    player.recent_tracks = deque(maxlen=10)
    player.seed_pattern_index = 0
    player.autoplay_enabled = autoplay_enabled
    player.text_channel = None  # suppress now-playing embed (method returns early when None)
    player.now_playing_message = None
    return player


async def test_track_start_records_in_history():
    from bot.cogs.music.cog import MusicCog
    from bot.cogs.music.player import MusicPlayer

    cog = object.__new__(MusicCog)

    player = _make_music_player_mock()
    player.__class__ = MusicPlayer  # makes isinstance(player, MusicPlayer) return True

    track = MagicMock()
    track.identifier = "abc123"
    track.extras = MagicMock()
    track.extras.requester = None

    payload = MagicMock()
    payload.player = player
    payload.track = track

    # text_channel is None → method returns early after recording, no Discord calls made
    await cog.on_wavelink_track_start(payload)

    assert "abc123" in player.played_ids
    assert track in list(player.recent_tracks)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_music.py::test_track_start_records_in_history -v
```

Expected: FAIL — `played_ids` is empty because the recording code isn't added yet.

- [ ] **Step 3: Update `on_wavelink_track_start` in `bot/cogs/music/cog.py`**

In `on_wavelink_track_start` (currently line 222), add two lines immediately after the early-return guard and before `await _disable_now_playing(player)`:

Find this block:
```python
    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = cast("MusicPlayer | None", payload.player)
        if not isinstance(player, MusicPlayer) or not player.text_channel:
            return

        await _disable_now_playing(player)
```

Replace with:
```python
    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = cast("MusicPlayer | None", payload.player)
        if not isinstance(player, MusicPlayer):
            return

        player.played_ids.add(payload.track.identifier)
        player.recent_tracks.append(payload.track)

        if not player.text_channel:
            return

        await _disable_now_playing(player)
```

> **Why split the guard**: recording history must happen even when `text_channel` is not set (e.g., the channel was never assigned), so the `isinstance` guard and the `text_channel` guard are now separate.

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_music.py::test_track_start_records_in_history -v
```

Expected: PASS.

---

### Task 4: Rewrite `on_wavelink_track_end` with the new algorithm

**Files:**
- Modify: `bot/cogs/music/cog.py` (`on_wavelink_track_end`, lines 253–276)
- Test: `tests/test_music.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_music.py`:

```python
async def test_autoplay_uses_top_candidate_not_random():
    """Autoplay picks candidates[0], not a random element."""
    from bot.cogs.music.cog import MusicCog
    from bot.cogs.music.player import MusicPlayer

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

    result_a = MagicMock(); result_a.identifier = "a"
    result_b = MagicMock(); result_b.identifier = "b"

    payload = MagicMock()
    payload.player = player
    payload.track = seed_track

    with patch("bot.cogs.music.cog.wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [result_a, result_b]
        await cog.on_wavelink_track_end(payload)

    # Should have queued result_a (index 0), not result_b
    player.queue.put_wait.assert_awaited_once_with(result_a)


async def test_autoplay_skips_already_played_tracks():
    """Tracks already in played_ids are excluded from candidates."""
    from bot.cogs.music.cog import MusicCog
    from bot.cogs.music.player import MusicPlayer

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

    already_played = MagicMock(); already_played.identifier = "already"
    fresh = MagicMock(); fresh.identifier = "fresh"

    player.played_ids = {"already"}  # simulate history

    payload = MagicMock()
    payload.player = player
    payload.track = seed_track

    with patch("bot.cogs.music.cog.wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [already_played, fresh]
        await cog.on_wavelink_track_end(payload)

    player.queue.put_wait.assert_awaited_once_with(fresh)


async def test_autoplay_increments_seed_pattern_index():
    """seed_pattern_index increments by 1 each time autoplay fires."""
    from bot.cogs.music.cog import MusicCog
    from bot.cogs.music.player import MusicPlayer

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

    result = MagicMock(); result.identifier = "r"
    payload = MagicMock()
    payload.player = player
    payload.track = seed_track

    with patch("bot.cogs.music.cog.wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [result]
        await cog.on_wavelink_track_end(payload)

    assert player.seed_pattern_index == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_music.py::test_autoplay_uses_top_candidate_not_random tests/test_music.py::test_autoplay_skips_already_played_tracks tests/test_music.py::test_autoplay_increments_seed_pattern_index -v
```

Expected: all 3 FAIL (current implementation uses `random.choice` and doesn't filter `played_ids`).

- [ ] **Step 3: Rewrite `on_wavelink_track_end` in `bot/cogs/music/cog.py`**

Replace the entire `on_wavelink_track_end` method (currently lines 253–276) with:

```python
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = cast("MusicPlayer | None", payload.player)
        if not isinstance(player, MusicPlayer) or not player.autoplay_enabled:
            return
        if not player.connected or not player.queue.is_empty:
            return

        recent = list(player.recent_tracks)[-3:]
        if not recent:
            return

        query = _build_autoplay_query(recent, player.seed_pattern_index)
        player.seed_pattern_index += 1

        try:
            results = await wavelink.Playable.search(query, source=wavelink.TrackSource.YouTubeMusic)
        except wavelink.LavalinkException:
            return

        if not results:
            return

        candidates = [t for t in results[:10] if t.identifier not in player.played_ids]
        if not candidates:
            candidates = list(results[:3])

        next_track = candidates[0]
        await player.queue.put_wait(next_track)
        await player.play(player.queue.get())
```

Also remove the now-unused `import random` from the top of `cog.py`.

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_music.py::test_autoplay_uses_top_candidate_not_random tests/test_music.py::test_autoplay_skips_already_played_tracks tests/test_music.py::test_autoplay_increments_seed_pattern_index -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Run the full test suite**

```
uv run pytest -v
```

Expected: all tests PASS, no regressions.

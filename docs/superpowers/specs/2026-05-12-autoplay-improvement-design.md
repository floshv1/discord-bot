# Autoplay Improvement Design

**Date**: 2026-05-12
**Status**: Approved

---

## Context

The current autoplay algorithm is unsatisfying in three ways:

1. **Same songs repeat** — no session history tracking; the same track can be suggested multiple times in one session
2. **Wrong vibe / irrelevant tracks** — the seed is always the single last track's artist name, so an outlier track derails the whole session
3. **Same-artist loop** — the algorithm always re-searches the same artist, creating a monotonous feedback loop

This design improves all three without adding external API dependencies.

---

## Approach

Core algorithm overhaul, self-contained (YouTube Music only). No new external services.

---

## Design

### 1. New State on MusicPlayer (`bot/cogs/music/player.py`)

Three new fields on `MusicPlayer`:

```python
played_ids: set[str]         # identifiers of every track played this session
recent_tracks: deque         # last 10 played tracks (maxlen=10), used for seed building
seed_pattern_index: int = 0  # cycles 0→1→2→0 to rotate search query patterns
```

All three are initialized in `__init__` and **reset on disconnect or `/stop`** so a new session starts clean.

### 2. Track Recording (`bot/cogs/music/cog.py`, `on_wavelink_track_start`)

Every track — manually queued or autoplay — is recorded as soon as it starts playing:

```python
player.played_ids.add(payload.track.identifier)
player.recent_tracks.append(payload.track)
```

Recording at `track_start` (not `track_end`) ensures that even a skipped track is excluded from future autoplay suggestions.

### 3. Seed Rotation Algorithm (`on_wavelink_track_end`)

`primary_artist` is derived from the **most-played artist in the last 3 `recent_tracks`** (ties broken by the most recent). This smooths out one-off outlier tracks.

The query pattern rotates on each autoplay trigger:

| Cycle (`index % 3`) | Pattern | Example |
|---------------------|---------|---------|
| 0 | `{primary_artist}` | `"Daft Punk"` |
| 1 | `{title} {artist}` | `"Get Lucky Daft Punk"` |
| 2 | `{primary_artist} mix` | `"Daft Punk mix"` |

### 4. Candidate Selection (`on_wavelink_track_end`)

```python
results = await wavelink.Playable.search(query, source=TrackSource.YouTubeMusic)
candidates = [t for t in results[:10] if t.identifier not in player.played_ids]
if not candidates:
    candidates = results[:3]  # fallback for very long sessions

next_track = candidates[0]            # top result = most relevant
next_track.extras.requester = None    # marks as autoplay in QueueView
```

Key changes from current behaviour:
- Pool **5 → 10** results (more candidates to filter from)
- Selection **random → top result** (randomness was compensating for absent deduplication)
- Fallback relaxes the filter only when the session is long enough that everything has already been played

---

## Files to Modify

| File | Change |
|------|--------|
| `bot/cogs/music/player.py` | Add `played_ids`, `recent_tracks`, `seed_pattern_index` fields |
| `bot/cogs/music/cog.py` | Update `on_wavelink_track_start` (record track), `on_wavelink_track_end` (new autoplay logic), `_disconnect` / stop handler (reset state) |

---

## Verification

1. **No repeats**: Queue 5 tracks manually, toggle autoplay ON, let the queue drain. Verify none of the 5 manually queued tracks appear again in autoplay suggestions.
2. **Seed rotation**: Watch the bot's debug logs or add temporary print statements — confirm the 3 query patterns cycle in order.
3. **Vibe context**: Queue 3 tracks by artist A, then 1 track by artist B. Let autoplay fire after artist B's track — confirm the bot seeds from artist A (most common in last 3), not artist B.
4. **Reset**: Use `/stop` then `/play` a new song — confirm `played_ids` is empty and seed pattern resets to 0.
5. **Long session fallback**: (Optional) Artificially populate `played_ids` with all result identifiers; confirm the bot falls back to top 3 rather than silently failing.

# Music Feature Design

**Date:** 2026-05-10
**Status:** Approved

## Context

Add music playback to the Discord bot. The bot currently has no audio capability. Goal: reliable, low-maintenance music with YouTube and Spotify support, an in-memory queue, autoplay recommendations, and auto-disconnect on idle.

**Stack:** wavelink 3.x (Python) + Lavalink 4 (Java audio server, Docker sidecar) + LavaSrc plugin (Spotify).

---

## Infrastructure

### New files
- `lavalink/application.yml` — Lavalink server config (YouTube enabled, LavaSrc plugin for Spotify)
- `lavalink/plugins/` — LavaSrc `.jar` (download from github.com/topi314/LavaSrc/releases)

### Modified files

| File | Change |
|---|---|
| `docker-compose.yml` | Add `lavalink` service (`ghcr.io/lavalink-devs/lavalink:4`) with volume mounts |
| `pyproject.toml` | Add `wavelink>=3.4` |
| `bot/core/config.py` | Add `lavalink_uri: str` and `lavalink_password: str` as optional env vars with defaults |
| `bot/core/bot.py` | Add `"bot.cogs.music.cog"` to `COGS` list |

**No database migration** — queue is fully in-memory.

**New optional env vars:**
- `LAVALINK_URI` — default `http://lavalink:2333`
- `LAVALINK_PASSWORD` — default `youshallnotpass`

---

## Code Structure

```
bot/cogs/music/
├── __init__.py
├── player.py       # MusicPlayer(wavelink.Player) — state + helpers
└── cog.py          # MusicCog(commands.Cog) — slash commands only
```

---

## `player.py` — MusicPlayer

Subclasses `wavelink.Player` to carry bot-specific state:

```python
class MusicPlayer(wavelink.Player):
    text_channel: discord.TextChannel   # where Now Playing embeds are sent
    autoplay_enabled: bool = False      # maps to wavelink.AutoPlayMode
    _idle_task: asyncio.Task | None = None
```

**Methods:**
- `start_idle_timer()` — 5-min `asyncio.sleep`; on fire: disconnect + send "Left due to inactivity" to `text_channel`
- `cancel_idle_timer()` — called when a new track starts
- `send_now_playing(track)` — embed to `text_channel` with title, duration, requester

**Autoplay:**
- `autoplay_enabled = True` → `wavelink.AutoPlayMode.enabled` (wavelink queues YouTube recommendations automatically)
- `autoplay_enabled = False` → `wavelink.AutoPlayMode.disabled`; idle timer starts when queue empties

---

## `cog.py` — Commands

| Command | Behavior |
|---|---|
| `/play <query>` | YouTube search or YouTube/Spotify URL. Join voice if needed. Add all tracks to queue end. Start playing if idle. Reply with "Added `<n>` tracks" or "Added `<title>`". |
| `/playnext <query>` | Same as `/play` but inserts after current track. For playlists, inserts only the first track. |
| `/skip` | Skip current track. If queue empty + autoplay off → idle timer starts. |
| `/stop` | Stop, clear queue, disconnect from voice. |
| `/list` | Embed: up to 10 tracks with position, title, duration, requester. Footer shows total queue duration. |
| `/remove <position>` | Remove track at 1-based position from queue. |
| `/autoplay` | Toggle autoplay. Reply with new state. |

### Guard checks

Every command: user must be in a voice channel.

`/skip`, `/stop`, `/list`, `/remove`, `/autoplay`: bot must be in the same voice channel as the user.

### Cog lifecycle

- `cog_load()` — connects to Lavalink via `wavelink.Pool.connect()`; if unreachable, logs error and skips gracefully
- `on_wavelink_track_end` — triggers idle timer (autoplay off) or lets wavelink handle recommendations (autoplay on)
- `on_voice_state_update` — if bot is left alone in voice channel, disconnect immediately

---

## Error Handling

All errors are ephemeral responses:

| Situation | Message |
|---|---|
| User not in voice | "You need to be in a voice channel first." |
| Bot in different voice | "I'm already playing in a different voice channel." |
| No results | "No results found for `<query>`." |
| Nothing playing | "Nothing is currently playing." |
| Invalid position | "Invalid position — queue only has `<n>` tracks." |
| Lavalink unreachable | Log error at startup; cog skips loading, other cogs unaffected |

---

## Verification

1. `docker compose up --build` — Lavalink starts healthy, bot logs "Connected to Lavalink node"
2. `/play never gonna give you up` — bot joins voice, plays, sends Now Playing embed
3. `/list` — shows queued tracks with durations
4. `/playnext <song>` — inserted track plays after current skip
5. `/skip` until empty — stays if autoplay on; disconnects after 5 min if off
6. `/autoplay` — toggle confirmed in reply
7. `/remove 1` — track removed, confirmed
8. `/stop` — bot leaves voice, queue cleared
9. Spotify URL in `/play` — plays via LavaSrc
10. All users leave voice — bot auto-disconnects

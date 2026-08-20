from __future__ import annotations

import re

from loguru import logger

_TITLE_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:official\s*(?:music\s*)?video|official\s*audio|official|"
    r"lyrics?|audio|hd|4k|mv|feat\.?\s*[^\)\]]+|ft\.?\s*[^\)\]]+)\s*[\)\]]"
    r"|\s*[-–]\s*(?:official\s*(?:music\s*)?video|official\s*audio|lyrics?)\s*$",
    re.IGNORECASE,
)

_AUTOPLAY_FILTER = re.compile(
    r"\b(?:live\s+(?:at|in|from|session|version|performance)|concert|en\s+direct"
    r"|live(?!\s+\w)|covered?\s+by|cover|tribute|remix|bootleg|extended\s+mix|vip\s+mix"
    r"|lyric\s+video|lyrics?|radio\s+edit)\b",
    re.IGNORECASE,
)

_FEAT_BARE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$", re.IGNORECASE)
_VEVO = re.compile(r"vevo$", re.IGNORECASE)
_ARTIST_SONG = re.compile(r"^(.+?)\s+[-–]\s+(.+)$")
# YouTube Music — which is where autoplay recommendations come from — credits tracks to
# an auto-generated "Artist - Topic" channel.
_TOPIC = re.compile(r"\s*[-–]\s*topic$", re.IGNORECASE)

# lrclib routinely takes ~6s to answer. The previous 5s budget timed out on essentially
# every request, so lyrics silently "never worked" — and TimeoutError stringifies to an
# empty message, which made the debug log look blank rather than damning.
LYRICS_TIMEOUT = 15

_SPOTIFY = re.compile(r"^\s*(?:spsearch:|(?:https?://)?(?:open\.spotify\.com|spotify\.link)/)", re.IGNORECASE)

NODE_UNAVAILABLE = "The music server isn't reachable right now — try again in a minute."
LOAD_FAILED = "Couldn't load that. Try a different search or URL."
SPOTIFY_UNCONFIGURED = "Spotify links aren't set up on this bot yet. Paste a YouTube link, or just search by name."

# Said out loud when Lavalink fails a track it had already accepted. The exception's own text
# never goes in here — same rule as `search_failure_message`: provider internals are not for
# members, and wavelink has already logged the full stacktrace for us.
PLAYBACK_FAILED = "⚠️ Couldn't play **{title}** — skipping it."
PLAYBACK_STUCK = "⚠️ **{title}** stalled with no audio — skipping it."


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_progress_bar(position_ms: int, length_ms: int, width: int = 15) -> str:
    if length_ms <= 0:
        return f"{'░' * width} 0:00 / 0:00"
    filled = round(min(position_ms / length_ms, 1.0) * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {_fmt_ms(position_ms)} / {_fmt_ms(length_ms)}"


def clean_title(title: str) -> str:
    return _TITLE_NOISE.sub("", title).strip().lower()


def titles_similar(a: str, b: str) -> bool:
    a_clean = clean_title(a)
    b_clean = clean_title(b)
    if not a_clean or not b_clean:
        return False
    return a_clean in b_clean or b_clean in a_clean


def calculate_eta(player: object, queue_insert_index: int) -> int:
    remaining = 0
    current = getattr(player, "current", None)
    if current:
        remaining = max(0, current.length - getattr(player, "position", 0))
    queue_tracks = list(getattr(player, "queue", []))
    return remaining + sum(t.length for t in queue_tracks[:queue_insert_index])


def is_spotify_query(query: str) -> bool:
    """True only for something Lavalink would hand to LavaSrc — a link or the `spsearch:` prefix.

    The word "spotify" typed inside an ordinary search is not one of those, and must not be
    answered with a setup message.
    """
    return bool(_SPOTIFY.match(query))


def search_failure_message(query: str, *, spotify_configured: bool) -> str:
    """What to tell the member when Lavalink refuses to load a query.

    A Spotify link on a bot with no `SPOTIFY_CLIENT_ID` is the one failure we can name
    exactly — LavaSrc cannot authenticate, so *every* Spotify link fails and retrying is
    pointless. Anything else could be a dead link, a region block or a bad day at YouTube,
    so it gets the generic nudge rather than a guess.
    """
    if not spotify_configured and is_spotify_query(query):
        return SPOTIFY_UNCONFIGURED
    return LOAD_FAILED


def is_filtered_autoplay_track(track: object) -> bool:
    title = getattr(track, "title", "") or ""
    return bool(_AUTOPLAY_FILTER.search(title))


def clean_for_lyrics(title: str, artist: str) -> tuple[str, str]:
    artist = _TOPIC.sub("", artist).strip()
    clean = _TITLE_NOISE.sub("", title).strip()
    m = _ARTIST_SONG.match(clean)
    if m:
        potential_artist, potential_song = m.group(1).strip(), m.group(2).strip()
        clean = potential_song
        if not artist or _VEVO.search(artist) or " " not in artist:
            artist = potential_artist
    clean = _FEAT_BARE.sub("", clean).strip()
    artist = _VEVO.sub("", artist).strip()
    return clean, artist


def pick_lyrics(results) -> str | None:
    """The first search hit that actually *has* lyrics.

    Taking results[0] blindly meant one instrumental or synced-only entry at the top of
    the list sank the whole lookup, even when the next hit had the lyrics.
    """
    for item in results or []:
        lyrics = (item.get("plainLyrics") or "").strip()
        if lyrics:
            return lyrics
    return None


async def fetch_lyrics(title: str, artist: str) -> str | None:
    import aiohttp

    clean, clean_artist = clean_for_lyrics(title, artist)
    logger.debug("fetch_lyrics: raw=({!r}, {!r}) clean=({!r}, {!r})", title, artist, clean, clean_artist)
    timeout = aiohttp.ClientTimeout(total=LYRICS_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(
                "https://lrclib.net/api/get",
                params={"track_name": clean, "artist_name": clean_artist},
            ) as resp:
                logger.debug("fetch_lyrics: /api/get status={}", resp.status)
                if resp.status != 404:
                    resp.raise_for_status()
                    data = await resp.json()
                    lyrics = (data.get("plainLyrics") or "").strip()
                    if lyrics:
                        return lyrics
        except Exception as e:
            # A TimeoutError's message is empty, so log the type or this reads as a blank.
            logger.debug("fetch_lyrics: /api/get failed: {}: {}", type(e).__name__, e)

        for query in (f"{clean} {clean_artist}".strip(), clean):
            try:
                async with session.get("https://lrclib.net/api/search", params={"q": query}) as resp:
                    logger.debug("fetch_lyrics: /api/search q={!r} status={}", query, resp.status)
                    resp.raise_for_status()
                    lyrics = pick_lyrics(await resp.json())
                    if lyrics:
                        return lyrics
            except Exception as e:
                logger.debug("fetch_lyrics: /api/search q={!r} failed: {}: {}", query, type(e).__name__, e)

    logger.debug("fetch_lyrics: all attempts exhausted for {!r}", title)
    return None

from __future__ import annotations

import re

_TITLE_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:official\s*(?:music\s*)?video|official\s*audio|official|"
    r"lyrics?|audio|hd|4k|mv|feat\.?\s*[^\)\]]+|ft\.?\s*[^\)\]]+)\s*[\)\]]"
    r"|\s*[-–]\s*(?:official\s*(?:music\s*)?video|official\s*audio|lyrics?)\s*$",
    re.IGNORECASE,
)

_AUTOPLAY_FILTER = re.compile(
    r"\b(?:live\s+(?:at|in|from|session|version|performance)|concert|en\s+direct"
    r"|live(?!\s+\w)|covered?\s+by|cover|tribute|remix|bootleg|extended\s+mix|vip\s+mix)\b",
    re.IGNORECASE,
)


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


def is_filtered_autoplay_track(track: object) -> bool:
    title = getattr(track, "title", "") or ""
    return bool(_AUTOPLAY_FILTER.search(title))


async def fetch_lyrics(title: str, artist: str) -> str | None:
    import aiohttp

    params = {"track_name": title, "artist_name": artist}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://lrclib.net/api/get",
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                data = await resp.json()
                return (data.get("plainLyrics") or "").strip() or None
    except Exception:
        return None

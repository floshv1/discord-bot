"""Images fetched on behalf of the browser: champion icons and the result splash.

Everything is re-encoded through Pillow (no source metadata, always a real PNG) and every failure is a
`HintUnavailable` whose text is for the logs only.
"""

from __future__ import annotations

import asyncio

import aiohttp

from bot.cogs.loldle import hints
from bot.cogs.loldle.catalog import Champion

ICON_URL = "https://cdn.communitydragon.org/latest/champion/{id}/square"
ICON_TIMEOUT = aiohttp.ClientTimeout(total=10)
ICON_MAX_BYTES = 1024 * 1024
CHUNK = 64 * 1024


async def fetch_icon(champion_id: str, http: aiohttp.ClientSession) -> bytes:
    """`champion_id` must already be a catalog id: it is interpolated into a URL."""
    try:
        async with http.get(ICON_URL.format(id=champion_id), timeout=ICON_TIMEOUT) as resp:
            if resp.status != 200:
                raise hints.HintUnavailable(f"HTTP {resp.status}")
            # `content.read(n)` returns whatever is buffered, not n bytes: read to EOF, bounded.
            chunks, size = [], 0
            async for chunk in resp.content.iter_chunked(CHUNK):
                size += len(chunk)
                if size > ICON_MAX_BYTES:
                    raise hints.HintUnavailable("image too large")
                chunks.append(chunk)
            body = b"".join(chunks)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise hints.HintUnavailable(type(exc).__name__) from exc
    return await _reencode(body)


async def render_splash(answer: Champion, http: aiohttp.ClientSession) -> bytes:
    """The full splash, only ever asked for once the member has found the champion."""
    return await _reencode(await hints.download_image(answer.splash, http))


async def _reencode(body: bytes) -> bytes:
    try:
        return await asyncio.to_thread(hints.reencode_png, body)
    except Exception as exc:  # noqa: BLE001 - Pillow raises a zoo of types on hostile input
        raise hints.HintUnavailable(type(exc).__name__) from exc

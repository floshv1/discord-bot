"""Hint content. Images are downloaded and re-encoded, then sent as `indice.png`: the CDN URLs carry
the champion's name (`…/champion/Ahri/ability-icon/q`, `Ahri_0.jpg`), so linking them would spoil it.

Rendering happens *before* the purchase, so a CDN failure costs the member nothing.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
from dataclasses import dataclass

import aiohttp
from PIL import Image

from bot.cogs.loldle import rules
from bot.cogs.loldle.catalog import Champion

FILENAME = "indice.png"
TIMEOUT = aiohttp.ClientTimeout(total=10)
MAX_BYTES = 10 * 1024 * 1024
SPLASH_SIZE = 320


class HintUnavailable(Exception):
    """The image could not be fetched or read. Safe to log, never shown as is."""


@dataclass(frozen=True)
class HintContent:
    text: str
    file_bytes: bytes | None = None


async def _download(url: str, session: aiohttp.ClientSession) -> bytes:
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                raise HintUnavailable(f"HTTP {resp.status}")
            body = await resp.read()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise HintUnavailable(type(exc).__name__) from exc
    if len(body) > MAX_BYTES:
        raise HintUnavailable("image too large")
    return body


def _encode(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.convert("RGBA").save(out, "PNG")  # re-encoded: no source metadata travels with it
    return out.getvalue()


def _reencode(body: bytes) -> bytes:
    with Image.open(io.BytesIO(body)) as image:
        return _encode(image)


def _crop(body: bytes, guild_id: int, day: dt.date) -> bytes:
    with Image.open(io.BytesIO(body)) as image:
        box = rules.crop_box(guild_id, day, image.width, image.height)
        return _encode(image.crop(box).resize((SPLASH_SIZE, SPLASH_SIZE), Image.Resampling.LANCZOS))


async def render_hint(
    kind: str, answer: Champion, guild_id: int, day: dt.date, session: aiohttp.ClientSession
) -> HintContent:
    if kind == "letter":
        return HintContent(f"🔤 Son nom commence par **{answer.name[0].upper()}**.")
    try:
        if kind == "spell":
            url = answer.spells[rules.spell_index(guild_id, day, len(answer.spells))]
            data = await asyncio.to_thread(_reencode, await _download(url, session))
            return HintContent("✨ Un de ses sorts :", data)
        data = await asyncio.to_thread(_crop, await _download(answer.splash, session), guild_id, day)
        return HintContent("🖼️ Un bout de son splash art :", data)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HintUnavailable(type(exc).__name__) from exc

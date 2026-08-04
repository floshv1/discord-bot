from __future__ import annotations

from typing import Any

import discord
from loguru import logger

from bot.cogs.music import service
from bot.cogs.music.embeds import build_history_embed, build_idle_embed, build_now_playing_embed
from bot.cogs.music.views import NowPlayingView
from bot.core.discord_utils import edit_if_changed


async def _partial(
    bot: discord.Client, guild_id: int, column: str
) -> tuple[discord.PartialMessage, discord.abc.Messageable] | None:
    """The pinned message named by ``column``, addressed without fetching it.

    ``get_partial_message`` costs no API call, so a redraw is exactly one request. Returns
    ``None`` when the guild has never run `/setup music`, when the channel is gone, or when
    the row has no message id — all three mean "there is no pinned card", which is a state
    the caller falls back from rather than an error.

    An unreachable database is folded into that same ``None``: losing the config must cost
    us the *pinned* card, not the Now-Playing card altogether. The caller then posts the
    transient one, which is the behaviour of a guild that never ran setup.
    """
    try:
        config = await service.get_config(guild_id)
    except Exception as exc:
        logger.warning(f"Could not read music config for guild {guild_id}: {exc}")
        return None
    if config is None or config[column] is None:
        return None
    channel = bot.get_channel(config["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return None
    return channel.get_partial_message(config[column]), channel


async def _edit(message: discord.PartialMessage, **kwargs: Any) -> None:
    """Edit a pinned card, skipping the request when nothing would change.

    Delegates to `edit_if_changed`, which owns the content guard, the deleted-card tolerance
    and the 429 back-off. The Now-Playing card is redrawn on every tick while a track plays,
    so without the guard this was one unconditional PATCH per tick, forever, on a single
    message id — see the docstring there.
    """
    await edit_if_changed(message, label="music", **kwargs)


async def is_configured(guild_id: int) -> bool:
    return await service.get_config(guild_id) is not None


async def resolve_channel(bot: discord.Client, guild_id: int) -> discord.TextChannel | None:
    """The configured music channel, or ``None`` — every piece of output routes through here."""
    config = await service.get_config(guild_id)
    if config is None:
        return None
    channel = bot.get_channel(config["channel_id"])
    return channel if isinstance(channel, discord.TextChannel) else None


async def now_playing_link(bot: discord.Client, guild_id: int) -> str | None:
    """A jump link to the pinned card, for `/nowplaying` to point at."""
    resolved = await _partial(bot, guild_id, "now_playing_message_id")
    return resolved[0].jump_url if resolved else None


async def redraw_now_playing(
    bot: discord.Client,
    guild_id: int,
    player: Any | None = None,
    reason: str | None = None,
) -> bool:
    """Redraw the pinned Now-Playing card. ``False`` means the guild has no card.

    A caller that gets ``False`` is in an unconfigured guild and must fall back to the old
    transient-message behaviour, or the member gets no feedback at all.
    """
    resolved = await _partial(bot, guild_id, "now_playing_message_id")
    if resolved is None:
        return False
    message, _ = resolved

    track = getattr(player, "current", None) if player else None
    if player is not None and track is not None:
        embed = build_now_playing_embed(track, player)
        view = NowPlayingView(player)
    else:
        embed = build_idle_embed(reason)
        view = NowPlayingView(idle=True)

    await _edit(message, embed=embed, view=view)
    return True


async def redraw_history(bot: discord.Client, guild_id: int) -> bool:
    """Redraw the pinned history card. ``False`` means the guild has no card."""
    resolved = await _partial(bot, guild_id, "history_message_id")
    if resolved is None:
        return False
    message, _ = resolved
    try:
        rows = await service.get_history(guild_id)
    except Exception as exc:
        logger.warning(f"Could not read music history for guild {guild_id}: {exc}")
        return False
    await _edit(message, embed=build_history_embed(rows))
    return True

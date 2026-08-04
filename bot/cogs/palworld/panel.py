from __future__ import annotations

import discord
from loguru import logger

from bot.cogs.palworld import service
from bot.cogs.palworld.embeds import build_panel_embed
from bot.cogs.palworld.service import ServerStatus
from bot.cogs.palworld.views import PalworldPanelView
from bot.core.discord_utils import edit_if_changed


async def _partial(bot: discord.Client, guild_id: int) -> discord.PartialMessage | None:
    """The pinned panel, addressed without fetching it.

    ``get_partial_message`` costs no API call, so a redraw is exactly one request.
    ``None`` covers every "there is no panel here" case — never set up, channel deleted,
    database unreachable — because losing the config must cost us the redraw, not take
    the whole ticker down with it.
    """
    try:
        config = await service.get_config(guild_id)
    except Exception as exc:
        logger.warning("Could not read the Palworld config for guild {}: {}", guild_id, exc)
        return None
    if config is None or config["panel_message_id"] is None:
        return None
    channel = bot.get_channel(config["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return None
    return channel.get_partial_message(config["panel_message_id"])


async def redraw(bot: discord.Client, guild_id: int, status: ServerStatus, address: str | None) -> None:
    """Repaint the panel, skipping the request when nothing would change.

    The ticker polls every 30s whether or not anyone is playing, and the embed carries a
    "Dernière vérification" timestamp — so every payload was unique and every tick spent a
    real edit re-rendering an identical card with a newer clock on it, forever, including
    on a server that has been off for a week. `edit_if_changed` excludes that timestamp
    from its comparison precisely so this card can sit still; it also owns the deleted-panel
    tolerance and the 429 back-off.
    """
    message = await _partial(bot, guild_id)
    if message is None:
        return
    await edit_if_changed(
        message,
        label="Palworld",
        embed=build_panel_embed(status, address),
        view=PalworldPanelView(status.status),
    )


async def announce(bot: discord.Client, guild_id: int, text: str) -> None:
    """Say something in the panel's channel — used when the server stops by itself.

    An automatic shutdown that leaves no trace looks like a crash. One line in the
    channel is the difference between "it works" and "it keeps dying".
    """
    try:
        config = await service.get_config(guild_id)
    except Exception:
        return
    if config is None:
        return
    channel = bot.get_channel(config["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.send(text)
    except discord.HTTPException as exc:
        logger.warning("Could not post the Palworld notice in {}: {}", config["channel_id"], exc)

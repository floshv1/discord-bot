from __future__ import annotations

import discord
from loguru import logger

from bot.cogs.palworld import service
from bot.cogs.palworld.embeds import build_panel_embed
from bot.cogs.palworld.service import ServerStatus
from bot.cogs.palworld.views import PalworldPanelView


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
    """Repaint the panel. Tolerates an admin who deleted it by hand.

    `/setup status` is what surfaces a missing panel, so swallowing NotFound here hides
    nothing — it just stops a deleted message from failing every tick from now on.
    """
    message = await _partial(bot, guild_id)
    if message is None:
        return
    try:
        await message.edit(embed=build_panel_embed(status, address), view=PalworldPanelView(status.status))
    except discord.NotFound:
        logger.debug("Palworld panel {} is gone — re-run /setup palworld.", message.id)
    except discord.HTTPException as exc:
        logger.warning("Could not edit the Palworld panel {}: {}", message.id, exc)


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

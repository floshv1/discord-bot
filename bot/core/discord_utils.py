from __future__ import annotations

import discord
from loguru import logger


async def pin(message: discord.Message) -> None:
    """Pin a bot-posted message, so a panel or board stays reachable once the channel scrolls.

    Everything called these "pinned" — the command descriptions, the docs — but nothing
    ever pinned them. Best-effort: missing Manage Messages, or a channel already at
    Discord's 50-pin cap, must not fail the whole setup.

    Lives here rather than in the setup cog because the betting boards pin themselves on a
    ticker, and setup imports the betting cog — so betting cannot import back from setup.
    """
    try:
        await message.pin()
    except discord.HTTPException as exc:
        logger.warning(f"Could not pin setup message {message.id}: {exc}")

from __future__ import annotations

import discord

from bot.db.client import get_pool


def ping_content(ping: discord.Role | None) -> str | None:
    """The message content that actually notifies people — or None to notify nobody.

    ``Role.mention`` returns ``<@&{id}>`` for *every* role, including @everyone. Discord
    does not notify anyone for that form: pinging everyone requires the literal string.
    Using ``.mention`` here would have looked correct and silently pinged no one.
    """
    if ping is None:
        return None
    if ping.is_default():
        return "@everyone"
    return ping.mention


def allowed_mentions_for(ping: discord.Role | None) -> discord.AllowedMentions:
    """Exactly who this announcement is permitted to notify — and nobody else.

    The body of an announcement goes in an *embed*, where mentions never ping, so this is
    the only thing standing between a typo and a server-wide notification. Default is
    silence: an admin has to name a role to wake anyone up.

    ``@everyone`` is the guild's default role, and Discord gates it behind the ``everyone``
    flag rather than ``roles`` — listing it as a role would silently fail to ping.
    """
    if ping is None:
        return discord.AllowedMentions.none()
    if ping.is_default():
        return discord.AllowedMentions(everyone=True, roles=False, users=False)
    return discord.AllowedMentions(everyone=False, roles=[ping], users=False)


async def get_announce_channel(guild_id: int) -> int | None:
    pool = get_pool()
    return await pool.fetchval("SELECT channel_id FROM announce_config WHERE guild_id = $1", guild_id)


async def set_announce_channel(guild_id: int, channel_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO announce_config (guild_id, channel_id)
        VALUES ($1, $2)
        ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id
        """,
        guild_id,
        channel_id,
    )

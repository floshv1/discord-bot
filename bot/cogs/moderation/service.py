from __future__ import annotations

import discord
from loguru import logger

from bot.cogs.logs.cog import make_embed
from bot.core.config import Config
from bot.db.client import get_pool

REPRIMAND_NICK = "Ennemi Public"

ACTION_COLORS: dict[str, discord.Color] = {
    "kick": discord.Color.red(),
    "ban": discord.Color.dark_red(),
    "unban": discord.Color.teal(),
    "timeout": discord.Color.orange(),
    "warn": discord.Color.yellow(),
    "reprimand": discord.Color.dark_orange(),
    "pardon": discord.Color.green(),
    "reprimand_expired": discord.Color.dark_orange(),
    "tribunal_guilty": discord.Color.dark_orange(),
    "tribunal_acquitted": discord.Color.green(),
}


async def log_action(
    client: discord.Client,
    config: Config,
    guild_id: int,
    target: discord.abc.User,
    moderator: discord.abc.User,
    action_type: str,
    reason: str | None,
) -> None:
    """Record a moderation action in ``mod_actions`` and mirror it to the log channel."""
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO mod_actions (guild_id, target_id, moderator_id, action_type, reason)
        VALUES ($1, $2, $3, $4, $5)
        """,
        guild_id,
        target.id,
        moderator.id,
        action_type,
        reason,
    )
    channel = client.get_channel(config.log_channel_id)
    if channel:
        details = f"{target.mention} ({target.id}) — by {moderator.mention}" + (f" — {reason}" if reason else "")
        color = ACTION_COLORS.get(action_type, discord.Color.greyple())
        await channel.send(embed=make_embed(color, action_type.replace("_", " ").title(), details))


async def deactivate_reprimand(reprimand_id: int) -> None:
    await get_pool().execute("UPDATE reprimands SET active = FALSE WHERE id = $1", reprimand_id)


async def lift_reprimand(member: discord.Member, original_nick: str | None) -> None:
    """Take the Ennemi Public role back off a member and restore their nickname.

    Lives here rather than on the cog because three callers need it: /pardon, the expiry
    ticker, and the tribunal acquitting someone. Reaching into the cog from the tribunal
    would make the import cycle unavoidable.
    """
    config = await get_pool().fetchrow("SELECT role_id FROM reprimand_config WHERE guild_id = $1", member.guild.id)
    if config:
        role = member.guild.get_role(config["role_id"])
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason="Reprimand lifted")
            except discord.Forbidden:
                logger.warning(f"Could not remove reprimand role from {member.id}")
    if member.nick == REPRIMAND_NICK:
        try:
            await member.edit(nick=original_nick, reason="Reprimand lifted")
        except discord.Forbidden:
            logger.warning(f"Could not restore nickname for {member.id}")

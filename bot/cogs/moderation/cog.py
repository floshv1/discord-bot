from __future__ import annotations

import datetime

import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger

from bot.cogs.logs.cog import make_embed
from bot.core.config import Config
from bot.db.client import get_pool


async def _log_action(
    bot: commands.Bot,
    config: Config,
    guild_id: int,
    target: discord.User | discord.Member,
    moderator: discord.Member,
    action_type: str,
    reason: str | None,
) -> None:
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
    channel = bot.get_channel(config.log_channel_id)
    if channel:
        details = f"{target.mention} ({target.id}) — by {moderator.mention}" + (f" — {reason}" if reason else "")
        color_map = {
            "kick": discord.Color.red(),
            "ban": discord.Color.dark_red(),
            "unban": discord.Color.teal(),
            "timeout": discord.Color.orange(),
            "warn": discord.Color.yellow(),
        }
        color = color_map.get(action_type, discord.Color.greyple())
        await channel.send(embed=make_embed(color, action_type.title(), details))


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]

    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(user="The member to kick", reason="Reason for the kick")
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None) -> None:
        await user.kick(reason=reason)
        await _log_action(self.bot, self.config, interaction.guild_id, user, interaction.user, "kick", reason)
        await interaction.response.send_message(f"Kicked {user.mention}.", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a member from the server.")
    @app_commands.describe(user="The member to ban", reason="Reason", delete_days="Days of messages to delete (0-7)")
    @app_commands.default_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
        delete_days: int = 0,
    ) -> None:
        await user.ban(reason=reason, delete_message_days=delete_days)
        await _log_action(self.bot, self.config, interaction.guild_id, user, interaction.user, "ban", reason)
        await interaction.response.send_message(f"Banned {user.mention}.", ephemeral=True)

    @app_commands.command(name="unban", description="Unban a user by their ID.")
    @app_commands.describe(user_id="The user ID to unban", reason="Reason")
    @app_commands.default_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str | None = None) -> None:
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("Invalid user ID.", ephemeral=True)
            return
        user = await self.bot.fetch_user(uid)
        await interaction.guild.unban(user, reason=reason)
        await _log_action(self.bot, self.config, interaction.guild_id, user, interaction.user, "unban", reason)
        await interaction.response.send_message(f"Unbanned {user.mention}.", ephemeral=True)

    @app_commands.command(name="timeout", description="Timeout a member.")
    @app_commands.describe(user="The member to timeout", duration="Duration in minutes", reason="Reason")
    @app_commands.default_permissions(kick_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: int,
        reason: str | None = None,
    ) -> None:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=duration)
        await user.timeout(until, reason=reason)
        await _log_action(self.bot, self.config, interaction.guild_id, user, interaction.user, "timeout", reason)
        await interaction.response.send_message(f"Timed out {user.mention} for {duration} minutes.", ephemeral=True)

    @app_commands.command(name="warn", description="Warn a member.")
    @app_commands.describe(user="The member to warn", reason="Reason for the warning")
    @app_commands.default_permissions(kick_members=True)
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str) -> None:
        await _log_action(self.bot, self.config, interaction.guild_id, user, interaction.user, "warn", reason)
        try:
            await user.send(f"You have received a warning in **{interaction.guild.name}**: {reason}")
        except discord.Forbidden:
            logger.warning(f"Could not DM warning to {user.id}")
        await interaction.response.send_message(f"Warned {user.mention}.", ephemeral=True)

    @app_commands.command(name="history", description="Show moderation history for a user.")
    @app_commands.describe(user="The user to look up")
    @app_commands.default_permissions(kick_members=True)
    async def history(self, interaction: discord.Interaction, user: discord.Member) -> None:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT action_type, reason, moderator_id, created_at
            FROM mod_actions
            WHERE guild_id = $1 AND target_id = $2
            ORDER BY created_at DESC
            LIMIT 10
            """,
            interaction.guild_id,
            user.id,
        )
        if not rows:
            await interaction.response.send_message(f"No moderation history for {user.mention}.", ephemeral=True)
            return

        lines = []
        for row in rows:
            ts = row["created_at"].strftime("%Y-%m-%d %H:%M")
            moderator = f"<@{row['moderator_id']}>"
            reason = row["reason"] or "no reason"
            lines.append(f"`{ts}` **{row['action_type']}** by {moderator} — {reason}")

        embed = discord.Embed(
            title=f"Mod history — {user.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clear", description="Delete messages in this channel.")
    @app_commands.describe(amount="Number of messages to scan (1–100)", user="Only delete messages from this user")
    @app_commands.default_permissions(manage_messages=True)
    async def clear(
        self,
        interaction: discord.Interaction,
        amount: int,
        user: discord.Member | None = None,
    ) -> None:
        if not 1 <= amount <= 100:
            await interaction.response.send_message("Amount must be between 1 and 100.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("Cannot access this channel.", ephemeral=True)
            return

        check = (lambda m: m.author == user) if user else (lambda m: True)
        try:
            deleted = await channel.purge(limit=amount, check=check, bulk=True)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to delete messages here.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"Failed to delete messages: {e}", ephemeral=True)
            return
        count = len(deleted)

        if count == 0:
            await interaction.followup.send("No messages to delete.", ephemeral=True)
            return

        reason = f"Cleared {count} message(s)" + (f" from {user}" if user else "")
        target_id = user.id if user else 0

        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO mod_actions (guild_id, target_id, moderator_id, action_type, reason)
            VALUES ($1, $2, $3, $4, $5)
            """,
            interaction.guild_id,
            target_id,
            interaction.user.id,
            "clear",
            reason,
        )

        log_channel = self.bot.get_channel(self.config.log_channel_id)
        if log_channel:
            details = (
                f"{channel.mention} — {count} message(s)"
                + (f" from {user.mention}" if user else "")
                + f" — by {interaction.user.mention}"
            )
            await log_channel.send(embed=make_embed(discord.Color.orange(), "Clear", details))

        await interaction.followup.send(f"Deleted {count} message(s).", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))

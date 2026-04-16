from __future__ import annotations

import datetime
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.core.config import Config
from bot.db.client import get_pool


def _fmt_duration(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        pool = get_pool()
        await pool.execute(
            """
            UPDATE voice_sessions
            SET left_at = NOW(),
                duration_seconds = GREATEST(EXTRACT(EPOCH FROM (NOW() - joined_at))::INT, 0)
            WHERE left_at IS NULL
            """
        )
        if self.config.voice_leaderboard_channel_id:
            await pool.execute(
                """
                INSERT INTO voice_leaderboard_config (guild_id, channel_id)
                VALUES ($1, $2)
                ON CONFLICT (guild_id) DO NOTHING
                """,
                self.config.guild_id,
                self.config.voice_leaderboard_channel_id,
            )
        self.refresh_leaderboard.start()

    async def cog_unload(self) -> None:
        self.refresh_leaderboard.cancel()

    @tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.UTC))
    async def refresh_leaderboard(self) -> None:
        await self._update_leaderboard_message()

    @refresh_leaderboard.before_loop
    async def before_refresh(self) -> None:
        await self.bot.wait_until_ready()

    async def _update_leaderboard_message(self) -> None:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT channel_id, message_id FROM voice_leaderboard_config WHERE guild_id = $1",
            self.config.guild_id,
        )
        if not row:
            return
        channel = self.bot.get_channel(row["channel_id"])
        if not channel:
            return
        embed = await self._build_leaderboard_embed("weekly")
        if row["message_id"]:
            try:
                msg = await channel.fetch_message(row["message_id"])
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                pass
        msg = await channel.send(embed=embed)
        await pool.execute(
            "UPDATE voice_leaderboard_config SET message_id = $1 WHERE guild_id = $2",
            msg.id,
            self.config.guild_id,
        )

    async def _build_leaderboard_embed(self, period: Literal["weekly", "alltime"]) -> discord.Embed:
        pool = get_pool()
        if period == "weekly":
            rows = await pool.fetch(
                """
                SELECT user_id, SUM(duration_seconds) AS total
                FROM voice_sessions
                WHERE guild_id = $1
                  AND left_at IS NOT NULL
                  AND joined_at >= date_trunc('week', NOW())
                GROUP BY user_id
                ORDER BY total DESC
                LIMIT 10
                """,
                self.config.guild_id,
            )
            title = "Voice Leaderboard — This Week"
        else:
            rows = await pool.fetch(
                """
                SELECT user_id, SUM(duration_seconds) AS total
                FROM voice_sessions
                WHERE guild_id = $1 AND left_at IS NOT NULL
                GROUP BY user_id
                ORDER BY total DESC
                LIMIT 10
                """,
                self.config.guild_id,
            )
            title = "Voice Leaderboard — All Time"

        embed = discord.Embed(title=title, color=discord.Color.purple())
        if not rows:
            embed.description = "No voice activity recorded yet."
            return embed

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            prefix = medals[i] if i < 3 else f"`{i + 1}.`"
            lines.append(f"{prefix} <@{row['user_id']}> — {_fmt_duration(row['total'] or 0)}")
        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Updated {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
        return embed

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        pool = get_pool()
        if before.channel is not None:
            await pool.execute(
                """
                UPDATE voice_sessions
                SET left_at = NOW(),
                    duration_seconds = GREATEST(EXTRACT(EPOCH FROM (NOW() - joined_at))::INT, 0)
                WHERE guild_id = $1 AND user_id = $2 AND left_at IS NULL
                """,
                member.guild.id,
                member.id,
            )
        if after.channel is not None:
            await pool.execute(
                "INSERT INTO voice_sessions (guild_id, user_id, channel_id) VALUES ($1, $2, $3)",
                member.guild.id,
                member.id,
                after.channel.id,
            )

    voice = app_commands.Group(name="voice", description="Voice channel tracking commands.")

    @voice.command(name="stats", description="Show voice stats for a user.")
    @app_commands.describe(user="User to look up (defaults to yourself)")
    async def voice_stats(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        pool = get_pool()

        weekly = await pool.fetchval(
            """
            SELECT COALESCE(SUM(duration_seconds), 0)
            FROM voice_sessions
            WHERE guild_id = $1 AND user_id = $2
              AND left_at IS NOT NULL
              AND joined_at >= date_trunc('week', NOW())
            """,
            interaction.guild_id,
            target.id,
        )
        alltime = await pool.fetchval(
            """
            SELECT COALESCE(SUM(duration_seconds), 0)
            FROM voice_sessions
            WHERE guild_id = $1 AND user_id = $2 AND left_at IS NOT NULL
            """,
            interaction.guild_id,
            target.id,
        )
        sessions = await pool.fetch(
            """
            SELECT channel_id, joined_at, duration_seconds
            FROM voice_sessions
            WHERE guild_id = $1 AND user_id = $2 AND left_at IS NOT NULL
            ORDER BY joined_at DESC
            LIMIT 5
            """,
            interaction.guild_id,
            target.id,
        )

        embed = discord.Embed(
            title=f"Voice stats — {target.display_name}",
            color=discord.Color.purple(),
        )
        embed.add_field(name="This week", value=_fmt_duration(weekly), inline=True)
        embed.add_field(name="All time", value=_fmt_duration(alltime), inline=True)

        if sessions:
            lines = []
            for s in sessions:
                channel = interaction.guild.get_channel(s["channel_id"])
                ch_name = f"#{channel.name}" if channel else "unknown"
                ts = s["joined_at"].strftime("%Y-%m-%d %H:%M")
                lines.append(f"`{ts}` {ch_name} — {_fmt_duration(s['duration_seconds'] or 0)}")
            embed.add_field(name="Recent sessions", value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed)

    @voice.command(name="leaderboard", description="Top 10 voice users.")
    @app_commands.describe(period="weekly or alltime")
    async def voice_leaderboard(
        self,
        interaction: discord.Interaction,
        period: Literal["weekly", "alltime"] = "weekly",
    ) -> None:
        embed = await self._build_leaderboard_embed(period)
        await interaction.response.send_message(embed=embed)

    @voice.command(name="setchannel", description="Set the persistent leaderboard channel.")
    @app_commands.describe(channel="Channel where the auto-updating leaderboard will be posted")
    @app_commands.default_permissions(manage_channels=True)
    async def voice_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO voice_leaderboard_config (guild_id, channel_id, message_id)
            VALUES ($1, $2, NULL)
            ON CONFLICT (guild_id) DO UPDATE SET channel_id = $2, message_id = NULL
            """,
            interaction.guild_id,
            channel.id,
        )
        await self._update_leaderboard_message()
        await interaction.response.send_message(
            f"Leaderboard channel set to {channel.mention}. Message posted!", ephemeral=True
        )
        logger.info(f"Voice leaderboard channel set to {channel.id} in guild {interaction.guild_id}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceCog(bot))

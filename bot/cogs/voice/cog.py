from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from loguru import logger

from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")


def _fmt_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.leaderboard_ticker.start()

    async def cog_unload(self) -> None:
        self.leaderboard_ticker.cancel()

    @tasks.loop(hours=1)
    async def leaderboard_ticker(self) -> None:
        await self._update_weekly_message()
        await self._update_alltime_message()

    @leaderboard_ticker.before_loop
    async def before_leaderboard_ticker(self) -> None:
        await self.bot.wait_until_ready()
        await self._initial_sync()

    @leaderboard_ticker.error
    async def leaderboard_ticker_error(self, error: BaseException) -> None:
        logger.warning("leaderboard_ticker error (will retry next tick): %s", error)

    async def _initial_sync(self) -> None:
        pool = get_pool()
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]

        await pool.execute(
            "UPDATE voice_sessions SET ended_at = NOW() WHERE guild_id = $1 AND ended_at IS NULL",
            guild_id,
        )

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        afk_channel = guild.afk_channel
        for channel in guild.voice_channels:
            if channel == afk_channel:
                continue
            for member in channel.members:
                if not member.bot:
                    await pool.execute(
                        "INSERT INTO voice_sessions (guild_id, user_id, channel_id) VALUES ($1, $2, $3)",
                        guild_id,
                        member.id,
                        channel.id,
                    )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if before.channel == after.channel:
            return

        pool = get_pool()
        guild_id = member.guild.id

        if before.channel is not None:
            await pool.execute(
                "UPDATE voice_sessions SET ended_at = NOW() WHERE guild_id = $1 AND user_id = $2 AND ended_at IS NULL",
                guild_id,
                member.id,
            )

        if after.channel is not None and after.channel != member.guild.afk_channel:
            await pool.execute(
                "INSERT INTO voice_sessions (guild_id, user_id, channel_id) VALUES ($1, $2, $3)",
                guild_id,
                member.id,
                after.channel.id,
            )

    async def _update_weekly_message(self) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        if not config.voice_leaderboard_channel_id:
            return

        pool = get_pool()
        guild_id = config.guild_id

        row = await pool.fetchrow(
            "SELECT channel_id, weekly_message_id FROM voice_leaderboard WHERE guild_id = $1",
            guild_id,
        )
        if not row or not row["weekly_message_id"]:
            return

        rows = await pool.fetch(
            """
            SELECT user_id,
                   SUM(EXTRACT(EPOCH FROM (COALESCE(ended_at, NOW()) - started_at))) AS total_seconds
            FROM voice_sessions
            WHERE guild_id = $1
              AND started_at > NOW() - INTERVAL '7 days'
            GROUP BY user_id
            ORDER BY total_seconds DESC
            LIMIT 10
            """,
            guild_id,
        )

        now = datetime.datetime.now(PARIS_TZ)
        embed = discord.Embed(title="🎙️ Voice — Last 7 Days", color=discord.Color.blurple())
        embed.set_footer(text=f"Updated {now.strftime('%d/%m at %H:%M')}")
        if not rows:
            embed.description = "No voice sessions this week."
        else:
            lines = []
            for i, r in enumerate(rows, 1):
                user = self.bot.get_user(r["user_id"])
                name = user.display_name if user else f"<@{r['user_id']}>"
                lines.append(f"**#{i}** {name} — {_fmt_duration(r['total_seconds'])}")
            embed.description = "\n".join(lines)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(row["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(row["weekly_message_id"])
            await msg.edit(embed=embed)
        except discord.HTTPException as e:
            logger.warning("Failed to update weekly leaderboard: %s", e)

    async def _update_alltime_message(self) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        if not config.voice_leaderboard_channel_id:
            return

        pool = get_pool()
        guild_id = config.guild_id

        row = await pool.fetchrow(
            "SELECT channel_id, alltime_message_id FROM voice_leaderboard WHERE guild_id = $1",
            guild_id,
        )
        if not row or not row["alltime_message_id"]:
            return

        rows = await pool.fetch(
            """
            SELECT user_id,
                   SUM(EXTRACT(EPOCH FROM (ended_at - started_at))) AS total_seconds
            FROM voice_sessions
            WHERE guild_id = $1 AND ended_at IS NOT NULL
            GROUP BY user_id
            ORDER BY total_seconds DESC
            LIMIT 10
            """,
            guild_id,
        )

        now = datetime.datetime.now(PARIS_TZ)
        embed = discord.Embed(title="🏆 Voice — All Time", color=discord.Color.gold())
        embed.set_footer(text=f"Updated {now.strftime('%d/%m at %H:%M')}")
        if not rows:
            embed.description = "No sessions recorded."
        else:
            lines = []
            for i, r in enumerate(rows, 1):
                user = self.bot.get_user(r["user_id"])
                name = user.display_name if user else f"<@{r['user_id']}>"
                lines.append(f"**#{i}** {name} — {_fmt_duration(r['total_seconds'])}")
            embed.description = "\n".join(lines)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(row["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(row["alltime_message_id"])
            await msg.edit(embed=embed)
        except discord.HTTPException as e:
            logger.warning("Failed to update all-time leaderboard: %s", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceCog(bot))

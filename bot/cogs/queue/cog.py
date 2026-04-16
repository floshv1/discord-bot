from __future__ import annotations

import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.core.config import Config
from bot.db.client import get_pool

DEFAULT_PRESETS = [
    ("lol", 5),
    ("overwatch", 5),
]


def _parse_start_time(time_str: str) -> datetime.datetime | None:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            t = datetime.time.fromisoformat(time_str) if ":" in time_str else None
            if t is None:
                continue
            t = datetime.datetime.strptime(time_str, fmt).time()
            now = datetime.datetime.now(tz=datetime.UTC)
            dt = datetime.datetime.combine(now.date(), t, tzinfo=datetime.UTC)
            if dt <= now:
                dt += datetime.timedelta(days=1)
            return dt
        except ValueError:
            continue
    return None


async def _game_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT name FROM game_presets WHERE guild_id = $1 AND name ILIKE $2 ORDER BY name LIMIT 10",
        interaction.guild_id,
        f"%{current}%",
    )
    return [app_commands.Choice(name=row["name"], value=row["name"]) for row in rows]


class QueueCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        pool = get_pool()
        for name, count in DEFAULT_PRESETS:
            await pool.execute(
                """
                INSERT INTO game_presets (guild_id, name, player_count)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, name) DO NOTHING
                """,
                self.config.guild_id,
                name,
                count,
            )
        self.queue_ticker.start()

    async def cog_unload(self) -> None:
        self.queue_ticker.cancel()

    @tasks.loop(minutes=1)
    async def queue_ticker(self) -> None:
        pool = get_pool()
        now = datetime.datetime.now(tz=datetime.UTC)
        expiry_cutoff = now - datetime.timedelta(hours=1)

        expired = await pool.fetch(
            """
            UPDATE game_queues
            SET status = 'cancelled'
            WHERE status = 'open' AND created_at < $1
            RETURNING id, channel_id, guild_id,
                      (SELECT name FROM game_presets WHERE id = preset_id) AS game_name
            """,
            expiry_cutoff,
        )
        for row in expired:
            channel = self.bot.get_channel(row["channel_id"])
            if channel:
                await channel.send(f"The **{row['game_name']}** queue has expired with no full lobby.")
            logger.info(f"Expired queue {row['id']} for game {row['game_name']} in guild {row['guild_id']}")

        remind_cutoff = now + datetime.timedelta(minutes=10)
        to_remind = await pool.fetch(
            """
            UPDATE game_queues
            SET reminder_sent = TRUE
            WHERE status IN ('open', 'filled')
              AND start_time IS NOT NULL
              AND start_time <= $1
              AND start_time > $2
              AND reminder_sent = FALSE
            RETURNING id, channel_id,
                      (SELECT name FROM game_presets WHERE id = preset_id) AS game_name
            """,
            remind_cutoff,
            now,
        )
        for row in to_remind:
            channel = self.bot.get_channel(row["channel_id"])
            if not channel:
                continue
            members = await pool.fetch("SELECT user_id FROM queue_members WHERE queue_id = $1", row["id"])
            mentions = " ".join(f"<@{m['user_id']}>" for m in members)
            await channel.send(f"⏰ **{row['game_name']}** starts in ~10 minutes! {mentions}")

    @queue_ticker.before_loop
    async def before_ticker(self) -> None:
        await self.bot.wait_until_ready()

    queue = app_commands.Group(name="queue", description="Game lobby queue commands.")

    @queue.command(name="join", description="Join (or create) a game queue.")
    @app_commands.describe(game="Game to queue for", start_time="Optional start time, e.g. 21:00 (UTC)")
    @app_commands.autocomplete(game=_game_autocomplete)
    async def queue_join(
        self,
        interaction: discord.Interaction,
        game: str,
        start_time: str | None = None,
    ) -> None:
        await interaction.response.defer()
        pool = get_pool()

        preset = await pool.fetchrow(
            "SELECT id, player_count FROM game_presets WHERE guild_id = $1 AND name = $2",
            interaction.guild_id,
            game.lower(),
        )
        if not preset:
            await interaction.followup.send(
                f"No preset found for **{game}**. Use `/queue add` to create one.", ephemeral=True
            )
            return

        parsed_time: datetime.datetime | None = None
        if start_time:
            parsed_time = _parse_start_time(start_time)
            if not parsed_time:
                await interaction.followup.send("Invalid time format. Use HH:MM, e.g. `21:00`.", ephemeral=True)
                return

        queue_row = await pool.fetchrow(
            "SELECT id, start_time FROM game_queues WHERE guild_id = $1 AND preset_id = $2 AND status = 'open'",
            interaction.guild_id,
            preset["id"],
        )

        if queue_row is None:
            queue_row = await pool.fetchrow(
                """
                INSERT INTO game_queues (guild_id, channel_id, preset_id, start_time)
                VALUES ($1, $2, $3, $4)
                RETURNING id, start_time
                """,
                interaction.guild_id,
                interaction.channel_id,
                preset["id"],
                parsed_time,
            )

        try:
            await pool.execute(
                "INSERT INTO queue_members (queue_id, user_id) VALUES ($1, $2)",
                queue_row["id"],
                interaction.user.id,
            )
        except Exception:
            await interaction.followup.send("You are already in this queue.", ephemeral=True)
            return

        members = await pool.fetch("SELECT user_id FROM queue_members WHERE queue_id = $1", queue_row["id"])
        count = len(members)
        needed = preset["player_count"]

        if count >= needed:
            await pool.execute(
                "UPDATE game_queues SET status = 'filled', filled_at = NOW() WHERE id = $1",
                queue_row["id"],
            )
            mentions = " ".join(f"<@{m['user_id']}>" for m in members)
            embed = discord.Embed(
                title=f"🎮 {game.upper()} lobby is ready!",
                color=discord.Color.green(),
            )
            embed.description = mentions
            if queue_row["start_time"]:
                ts = int(queue_row["start_time"].timestamp())
                embed.add_field(name="Starting at", value=f"<t:{ts}:t> — reminder 10 min before!", inline=False)
            await interaction.followup.send(embed=embed)
        else:
            embed = discord.Embed(
                title=f"🎮 {game.upper()} queue — {count}/{needed}",
                color=discord.Color.blurple(),
            )
            embed.description = " ".join(f"<@{m['user_id']}>" for m in members)
            if queue_row["start_time"]:
                ts = int(queue_row["start_time"].timestamp())
                embed.add_field(name="Scheduled start", value=f"<t:{ts}:t>", inline=False)
            await interaction.followup.send(embed=embed)

    @queue.command(name="leave", description="Leave a game queue.")
    @app_commands.describe(game="Game queue to leave")
    @app_commands.autocomplete(game=_game_autocomplete)
    async def queue_leave(self, interaction: discord.Interaction, game: str) -> None:
        pool = get_pool()
        preset = await pool.fetchrow(
            "SELECT id FROM game_presets WHERE guild_id = $1 AND name = $2",
            interaction.guild_id,
            game.lower(),
        )
        if not preset:
            await interaction.response.send_message(f"No preset found for **{game}**.", ephemeral=True)
            return

        queue_row = await pool.fetchrow(
            "SELECT id FROM game_queues WHERE guild_id = $1 AND preset_id = $2 AND status = 'open'",
            interaction.guild_id,
            preset["id"],
        )
        if not queue_row:
            await interaction.response.send_message(f"No open **{game}** queue found.", ephemeral=True)
            return

        deleted = await pool.execute(
            "DELETE FROM queue_members WHERE queue_id = $1 AND user_id = $2",
            queue_row["id"],
            interaction.user.id,
        )
        if deleted == "DELETE 0":
            await interaction.response.send_message("You are not in this queue.", ephemeral=True)
            return

        remaining = await pool.fetchval("SELECT COUNT(*) FROM queue_members WHERE queue_id = $1", queue_row["id"])
        if remaining == 0:
            await pool.execute("UPDATE game_queues SET status = 'cancelled' WHERE id = $1", queue_row["id"])
            await interaction.response.send_message(f"Left the **{game}** queue. Queue cancelled (no members left).")
        else:
            await interaction.response.send_message(f"Left the **{game}** queue.")

    @queue.command(name="list", description="List all open game queues.")
    async def queue_list(self, interaction: discord.Interaction) -> None:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT gq.id, gp.name, gp.player_count, gq.start_time,
                   COUNT(qm.id) AS member_count
            FROM game_queues gq
            JOIN game_presets gp ON gp.id = gq.preset_id
            LEFT JOIN queue_members qm ON qm.queue_id = gq.id
            WHERE gq.guild_id = $1 AND gq.status = 'open'
            GROUP BY gq.id, gp.name, gp.player_count, gq.start_time
            ORDER BY gq.created_at
            """,
            interaction.guild_id,
        )
        if not rows:
            await interaction.response.send_message("No open queues right now.", ephemeral=True)
            return

        embed = discord.Embed(title="Open queues", color=discord.Color.blurple())
        for row in rows:
            value = f"{row['member_count']}/{row['player_count']} players"
            if row["start_time"]:
                ts = int(row["start_time"].timestamp())
                value += f" — starts <t:{ts}:t>"
            embed.add_field(name=row["name"].upper(), value=value, inline=False)
        await interaction.response.send_message(embed=embed)

    @queue.command(name="add", description="Add a custom game preset.")
    @app_commands.describe(game="Game name", player_count="Number of players needed")
    @app_commands.default_permissions(kick_members=True)
    async def queue_add(self, interaction: discord.Interaction, game: str, player_count: int) -> None:
        if player_count < 2 or player_count > 100:
            await interaction.response.send_message("Player count must be between 2 and 100.", ephemeral=True)
            return
        pool = get_pool()
        try:
            await pool.execute(
                "INSERT INTO game_presets (guild_id, name, player_count) VALUES ($1, $2, $3)",
                interaction.guild_id,
                game.lower(),
                player_count,
            )
        except Exception:
            await interaction.response.send_message(f"A preset for **{game}** already exists.", ephemeral=True)
            return
        await interaction.response.send_message(f"Added preset **{game}** ({player_count} players).", ephemeral=True)

    @queue.command(name="remove", description="Remove a game preset.")
    @app_commands.describe(game="Game preset to remove")
    @app_commands.autocomplete(game=_game_autocomplete)
    @app_commands.default_permissions(kick_members=True)
    async def queue_remove(self, interaction: discord.Interaction, game: str) -> None:
        pool = get_pool()
        deleted = await pool.execute(
            "DELETE FROM game_presets WHERE guild_id = $1 AND name = $2",
            interaction.guild_id,
            game.lower(),
        )
        if deleted == "DELETE 0":
            await interaction.response.send_message(f"No preset found for **{game}**.", ephemeral=True)
            return
        await interaction.response.send_message(f"Removed preset **{game}**.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QueueCog(bot))

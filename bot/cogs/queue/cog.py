from __future__ import annotations

import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.queue import service
from bot.cogs.queue.embeds import build_panel_embed
from bot.cogs.queue.views import PanelView, make_card_view
from bot.core.config import Config
from bot.db.client import get_pool

DEFAULT_PRESETS = [
    ("lol", 5),
    ("overwatch", 5),
]

# Lifecycle thresholds.
AUTO_CLOSE_AFTER_START = datetime.timedelta(minutes=30)
IDLE_EXPIRY_OPEN = datetime.timedelta(minutes=45)
IDLE_EXPIRY_FILLED = datetime.timedelta(hours=2)
REMINDER_LEAD = datetime.timedelta(minutes=10)


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

        # Re-register the persistent panel view (if configured) and refresh its message.
        await self.refresh_panel()

        # Re-register persistent views for every active queue card.
        active = await pool.fetch(
            "SELECT id, status FROM game_queues WHERE status IN ('open', 'filled') AND guild_id = $1",
            self.config.guild_id,
        )
        for row in active:
            self.bot.add_view(make_card_view(row["id"], row["status"]))

        self.queue_ticker.start()

    async def cog_unload(self) -> None:
        self.queue_ticker.cancel()

    async def refresh_panel(self) -> None:
        """Rebuild and edit the control-panel message so its game buttons match the presets."""
        config = await service.get_queue_config(self.config.guild_id)
        if not config or not config["panel_message_id"]:
            return
        channel = self.bot.get_channel(config["channel_id"])
        if channel is None:
            return
        presets = await service.list_presets(self.config.guild_id)
        view = PanelView(presets)
        self.bot.add_view(view)
        try:
            msg = await channel.fetch_message(config["panel_message_id"])
            await msg.edit(embed=build_panel_embed(), view=view)
        except discord.NotFound:
            logger.warning("Queue panel message not found; run /setup queue again.")

    async def _remove_card(self, row) -> None:
        if not (row["channel_id"] and row["message_id"]):
            return
        channel = self.bot.get_channel(row["channel_id"])
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(row["message_id"])
            await msg.delete()
        except discord.NotFound:
            pass

    @tasks.loop(minutes=1)
    async def queue_ticker(self) -> None:
        pool = get_pool()
        now = datetime.datetime.now(tz=datetime.UTC)

        # 1. Auto-close queues whose start time passed (host forgot to close).
        closed = await pool.fetch(
            """
            UPDATE game_queues SET status = 'done'
            WHERE status IN ('open', 'filled')
              AND start_time IS NOT NULL AND start_time < $1
            RETURNING id, channel_id, message_id,
                      (SELECT name FROM game_presets WHERE id = preset_id) AS game_name
            """,
            now - AUTO_CLOSE_AFTER_START,
        )
        for row in closed:
            await self._remove_card(row)
            logger.info(f"Auto-closed queue {row['id']} ({row['game_name']}) past start time")

        # 2. Idle-expiry safety net for queues without a start time.
        expired_open = await pool.fetch(
            """
            UPDATE game_queues SET status = 'cancelled'
            WHERE status = 'open' AND start_time IS NULL AND last_activity_at < $1
            RETURNING id, channel_id, message_id,
                      (SELECT name FROM game_presets WHERE id = preset_id) AS game_name
            """,
            now - IDLE_EXPIRY_OPEN,
        )
        expired_filled = await pool.fetch(
            """
            UPDATE game_queues SET status = 'cancelled'
            WHERE status = 'filled' AND start_time IS NULL AND last_activity_at < $1
            RETURNING id, channel_id, message_id,
                      (SELECT name FROM game_presets WHERE id = preset_id) AS game_name
            """,
            now - IDLE_EXPIRY_FILLED,
        )
        for row in (*expired_open, *expired_filled):
            await self._remove_card(row)
            logger.info(f"Expired idle queue {row['id']} ({row['game_name']})")

        # 3. Start-time reminders (~10 min before).
        to_remind = await pool.fetch(
            """
            UPDATE game_queues SET reminder_sent = TRUE
            WHERE status IN ('open', 'filled')
              AND start_time IS NOT NULL
              AND start_time <= $1 AND start_time > $2
              AND reminder_sent = FALSE
            RETURNING id, channel_id,
                      (SELECT name FROM game_presets WHERE id = preset_id) AS game_name
            """,
            now + REMINDER_LEAD,
            now,
        )
        for row in to_remind:
            channel = self.bot.get_channel(row["channel_id"])
            if channel is None:
                continue
            members = await pool.fetch(
                "SELECT user_id FROM queue_members WHERE queue_id = $1 AND in_lane = FALSE AND cant_attend = FALSE",
                row["id"],
            )
            mentions = " ".join(f"<@{m['user_id']}>" for m in members)
            await channel.send(f"⏰ **{row['game_name'].upper()}** starts in ~10 minutes! {mentions}")

    @queue_ticker.error
    async def queue_ticker_error(self, error: BaseException) -> None:
        # Without this, one exception permanently kills the loop and auto-close, idle
        # expiry and start reminders all stop silently until the next restart.
        logger.warning(f"queue_ticker error (will retry next tick): {error}")

    @queue_ticker.before_loop
    async def before_ticker(self) -> None:
        await self.bot.wait_until_ready()

    queue = app_commands.Group(name="queue", description="Game lobby queue commands.")

    @queue.command(name="add", description="Add a game preset (shows as a button on the queue panel).")
    @app_commands.describe(game="Game name", player_count="Default number of players")
    @app_commands.default_permissions(kick_members=True)
    async def queue_add(self, interaction: discord.Interaction, game: str, player_count: int) -> None:
        if player_count < 2 or player_count > 100:
            await interaction.response.send_message("Player count must be between 2 and 100.", ephemeral=True)
            return
        # on_panel=True also promotes an existing ad-hoc preset (one a member created via
        # the panel's "Other" button) into a permanent panel button.
        await service.upsert_preset(interaction.guild_id, game.lower(), player_count, on_panel=True)
        await self.refresh_panel()
        await interaction.response.send_message(f"Added preset **{game}** ({player_count} players).", ephemeral=True)

    @queue.command(name="remove", description="Remove a game preset.")
    @app_commands.describe(game="Game preset to remove")
    @app_commands.autocomplete(game=_game_autocomplete)
    @app_commands.default_permissions(kick_members=True)
    async def queue_remove(self, interaction: discord.Interaction, game: str) -> None:
        pool = get_pool()
        preset = await pool.fetchrow(
            "SELECT id FROM game_presets WHERE guild_id = $1 AND name = $2",
            interaction.guild_id,
            game.lower(),
        )
        if not preset:
            await interaction.response.send_message(f"No preset found for **{game}**.", ephemeral=True)
            return

        active = await pool.fetchval(
            "SELECT COUNT(*) FROM game_queues WHERE preset_id = $1 AND status IN ('open', 'filled')",
            preset["id"],
        )
        if active:
            await interaction.response.send_message(
                f"**{game}** still has active queues — close them first.", ephemeral=True
            )
            return

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM queue_members WHERE queue_id IN (SELECT id FROM game_queues WHERE preset_id = $1)",
                    preset["id"],
                )
                await conn.execute("DELETE FROM game_queues WHERE preset_id = $1", preset["id"])
                await conn.execute("DELETE FROM game_presets WHERE id = $1", preset["id"])

        await self.refresh_panel()
        await interaction.response.send_message(f"Removed preset **{game}**.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QueueCog(bot))

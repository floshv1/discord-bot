from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.birthday.cog import MONTHS_EN, BirthdayCog
from bot.cogs.queue import service as queue_service
from bot.cogs.queue.embeds import build_panel_embed
from bot.cogs.queue.views import PanelView
from bot.cogs.suggestions.cog import SetupView
from bot.cogs.voice.cog import VoiceCog
from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")


class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    setup = app_commands.Group(name="setup", description="Initialize bot features (moderators).")

    @setup.command(name="voice", description="Initialize voice leaderboard messages.")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_voice(self, interaction: discord.Interaction) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        if not config.voice_leaderboard_channel_id:
            await interaction.response.send_message(
                "❌ `VOICE_LEADERBOARD_CHANNEL_ID` is not configured.", ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(config.voice_leaderboard_channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        weekly_msg = await channel.send(
            embed=discord.Embed(title="🎙️ Voice — Last 7 Days", description="Loading...", color=discord.Color.blurple())
        )
        alltime_msg = await channel.send(
            embed=discord.Embed(title="🏆 Voice — All Time", description="Loading...", color=discord.Color.gold())
        )
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO voice_leaderboard (guild_id, channel_id, weekly_message_id, alltime_message_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id) DO UPDATE
              SET channel_id = EXCLUDED.channel_id,
                  weekly_message_id = EXCLUDED.weekly_message_id,
                  alltime_message_id = EXCLUDED.alltime_message_id
            """,
            interaction.guild_id,
            channel.id,
            weekly_msg.id,
            alltime_msg.id,
        )
        cog: VoiceCog | None = self.bot.cogs.get("VoiceCog")  # type: ignore[assignment]
        if cog:
            await cog._update_weekly_message()
            await cog._update_alltime_message()
        await interaction.followup.send("✅ Voice leaderboard initialized.", ephemeral=True)

    @setup.command(name="birthday", description="Initialize birthday pinned messages.")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_birthday(self, interaction: discord.Interaction) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        if not config.birthday_channel_id:
            await interaction.response.send_message("❌ `BIRTHDAY_CHANNEL_ID` is not configured.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(config.birthday_channel_id)
        if not channel:
            await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        now = datetime.datetime.now(PARIS_TZ)
        month_name = MONTHS_EN[now.month - 1]
        upcoming_msg = await channel.send(
            embed=discord.Embed(title="🎉 Upcoming Birthdays", description="Loading...", color=discord.Color.blue())
        )
        month_msg = await channel.send(
            embed=discord.Embed(
                title=f"📅 {month_name} Birthdays",
                description="Loading...",
                color=discord.Color.purple(),
            )
        )
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO birthday_config (guild_id, channel_id, upcoming_message_id, month_message_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id) DO UPDATE
              SET channel_id = EXCLUDED.channel_id,
                  upcoming_message_id = EXCLUDED.upcoming_message_id,
                  month_message_id = EXCLUDED.month_message_id
            """,
            interaction.guild_id,
            channel.id,
            upcoming_msg.id,
            month_msg.id,
        )
        cog: BirthdayCog | None = self.bot.cogs.get("BirthdayCog")  # type: ignore[assignment]
        if cog:
            await cog._update_upcoming_embed()
            await cog._update_month_embed()
        await interaction.followup.send("✅ Birthday messages initialized.", ephemeral=True)

    @setup.command(name="suggestions", description="Post the suggestion entry-point message in a channel.")
    @app_commands.describe(channel="Channel where suggestions will be collected")
    @app_commands.default_permissions(manage_channels=True)
    async def setup_suggestions(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        embed = discord.Embed(
            title="💡 Suggestions",
            description=(
                "Have an idea to make the server better?\n\n"
                "✨ **New Feature** — suggest something brand new\n"
                "🔧 **Improvement** — improve an existing feature"
            ),
            color=discord.Color.blurple(),
        )
        view = SetupView()
        msg = await channel.send(embed=embed, view=view)
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO suggestion_config (guild_id, channel_id, message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id) DO UPDATE SET channel_id = $2, message_id = $3
            """,
            interaction.guild_id,
            channel.id,
            msg.id,
        )
        await interaction.response.send_message(f"Suggestion channel set to {channel.mention}!", ephemeral=True)

    @setup.command(name="reprimand", description="Configure the Ennemi Public role and goulag channel.")
    @app_commands.describe(role="Role applied to reprimanded members", channel="Channel they're restricted to")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_reprimand(
        self, interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel
    ) -> None:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO reprimand_config (guild_id, role_id, channel_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id) DO UPDATE SET role_id = $2, channel_id = $3
            """,
            interaction.guild_id,
            role.id,
            channel.id,
        )
        await interaction.response.send_message(
            f"Reprimand configured: role {role.mention}, channel {channel.mention}.", ephemeral=True
        )

    @setup.command(name="queue", description="Post the game-queue control panel in a channel.")
    @app_commands.describe(channel="Channel that will host the queue panel and queue cards")
    @app_commands.default_permissions(manage_channels=True)
    async def setup_queue(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True)
        presets = await queue_service.list_presets(interaction.guild_id)
        view = PanelView(presets)
        msg = await channel.send(embed=build_panel_embed(), view=view)
        self.bot.add_view(view)
        await queue_service.set_queue_config(interaction.guild_id, channel.id, msg.id)
        await interaction.followup.send(f"Queue panel posted in {channel.mention}!", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupCog(bot))

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.help.embeds import build_help_embed
from bot.db.client import get_pool

# Feature key → the table whose row proves an admin has set it up, plus an optional column
# that must also be non-NULL (a feature whose config row exists before it is usable).
FEATURE_TABLES: dict[str, tuple[str, str | None]] = {
    "currency": ("currency_leaderboard", None),
    "betting": ("betting_config", None),
    "music": ("music_config", None),
    "palworld": ("palworld_config", None),
}


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="What can this bot do?")
    async def help_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        pool = get_pool()

        channels: dict[str, str | None] = {}
        for key, (table, guard) in FEATURE_TABLES.items():
            selected = "channel_id" + (f", {guard}" if guard else "")
            row = await pool.fetchrow(
                f"SELECT {selected} FROM {table} WHERE guild_id = $1",  # noqa: S608 - names are literals
                interaction.guild_id,
            )
            if row and guard and row[guard] is None:
                row = None
            channel = self.bot.get_channel(row["channel_id"]) if row else None
            channels[key] = channel.mention if channel else None

        is_mod = interaction.user.guild_permissions.manage_messages
        await interaction.followup.send(embed=build_help_embed(channels, is_mod), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))

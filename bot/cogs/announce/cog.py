from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.announce import service
from bot.cogs.announce.views import AnnouncementModal


class AnnounceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="announce", description="Publier une annonce dans le salon d'annonces.")
    @app_commands.describe(ping="Rôle à mentionner (@everyone accepté). Par défaut : personne.")
    @app_commands.default_permissions(manage_guild=True)
    async def announce(self, interaction: discord.Interaction, ping: discord.Role | None = None) -> None:
        channel_id = await service.get_announce_channel(interaction.guild_id)
        if channel_id is None:
            await interaction.response.send_message(
                "❌ Aucun salon d'annonces configuré. Lance `/setup announce #salon` d'abord.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            await interaction.response.send_message(
                "❌ Le salon d'annonces configuré n'existe plus. Relance `/setup announce #salon`.",
                ephemeral=True,
            )
            return

        # Straight to the modal: it's the only input that takes newlines, and the preview it
        # opens is where the announcement actually gets checked before going out.
        await interaction.response.send_modal(AnnouncementModal(channel, ping))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnnounceCog(bot))

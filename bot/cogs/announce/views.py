from __future__ import annotations

import discord
from loguru import logger

from bot.cogs.announce.embeds import BODY_LIMIT, TITLE_LIMIT, build_announcement_embed
from bot.cogs.announce.service import allowed_mentions_for


class AnnouncementModal(discord.ui.Modal, title="Nouvelle annonce"):
    """The only Discord input that accepts newlines.

    A slash-command string argument physically cannot contain them, which would flatten any
    real announcement into one unreadable paragraph.
    """

    title_input = discord.ui.TextInput(
        label="Titre",
        placeholder="🤖 Mise à jour du bot",
        required=True,
        max_length=TITLE_LIMIT,
    )
    body_input = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="## ✨ Nouveau\n`/help` — tout ce que le bot sait faire",
        required=True,
        max_length=BODY_LIMIT,
    )

    def __init__(self, channel: discord.TextChannel, ping: discord.Role | None) -> None:
        super().__init__()
        self.channel = channel
        self.ping = ping

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = build_announcement_embed(
            title=str(self.title_input),
            body=str(self.body_input),
            author_name=interaction.user.display_name,
        )
        view = PreviewView(self.channel, self.ping, embed)
        ping_line = f"Ping : {self.ping.mention}" if self.ping else "Aucun ping."
        await interaction.response.send_message(
            f"**Aperçu** — voici ce qui sera publié dans {self.channel.mention}.\n{ping_line}",
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),  # the preview must never ping
        )


class PreviewView(discord.ui.View):
    """Publish / cancel. Ephemeral and short-lived, so a timeout is fine here.

    An announcement is irreversible in practice — deleting it after 200 people have read it
    changes nothing — so it gets one deliberate confirmation.
    """

    def __init__(self, channel: discord.TextChannel, ping: discord.Role | None, embed: discord.Embed) -> None:
        super().__init__(timeout=300)
        self.channel = channel
        self.ping = ping
        self.embed = embed

    @discord.ui.button(label="Publier", emoji="📢", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await self.channel.send(
                content=self.ping.mention if self.ping else None,
                embed=self.embed,
                allowed_mentions=allowed_mentions_for(self.ping),
            )
        except discord.Forbidden:
            await interaction.response.edit_message(
                content=f"❌ Je n'ai pas la permission d'écrire dans {self.channel.mention}.",
                embed=None,
                view=None,
            )
            return
        except discord.HTTPException as exc:
            logger.warning(f"Failed to publish announcement: {exc}")
            await interaction.response.edit_message(
                content="❌ Discord a refusé l'annonce. Réessaie dans un instant.", embed=None, view=None
            )
            return

        await interaction.response.edit_message(
            content=f"✅ Annonce publiée dans {self.channel.mention}.", embed=None, view=None
        )

    @discord.ui.button(label="Annuler", emoji="❌", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Annonce annulée.", embed=None, view=None)

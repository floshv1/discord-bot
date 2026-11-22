"""Hint buttons on the private card. Ephemeral, so they may time out: `/loldle` shows them again."""

from __future__ import annotations

import datetime as dt

import discord
from loguru import logger

from bot.cogs.loldle import rules

LABELS = {"spell": "Sort", "splash": "Bout de splash", "letter": "Première lettre"}
TIMEOUT = 14 * 60  # an interaction token dies at 15 minutes


class HintButton(discord.ui.Button):
    def __init__(self, kind: str, day: dt.date, *, owned: bool, enabled: bool) -> None:
        label = f"👁️ {LABELS[kind]}" if owned else f"💡 {LABELS[kind]} · {rules.HINTS[kind]} 🪙"
        style = discord.ButtonStyle.success if owned else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, disabled=not enabled, custom_id=f"loldle:hint:{kind}:{day:%Y%m%d}")
        self.kind, self.day = kind, day

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("LoldleCog")  # type: ignore[attr-defined]
        if cog is None:
            await interaction.response.send_message("Le LoLdle n'est pas disponible.", ephemeral=True)
            return
        await cog.buy(interaction, self.kind, self.day)


class GameView(discord.ui.View):
    def __init__(self, day: dt.date, owned: set[str], guesses: int, solved: bool) -> None:
        super().__init__(timeout=TIMEOUT)
        for kind in rules.HINTS:
            has = kind in owned
            buyable = not solved and rules.can_buy(kind, owned, guesses)
            self.add_item(HintButton(kind, day, owned=has, enabled=has or buyable))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        # `tree.on_error` never sees views: without this a failure stays on « is thinking… ».
        logger.opt(exception=error).error("LoLdle hint button failed")
        text = "Quelque chose a échoué. Rien n'a été débité si l'indice ne s'est pas affiché."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except (discord.HTTPException, discord.ClientException):
            logger.warning("LoLdle: could not report a button failure")

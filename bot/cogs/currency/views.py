from __future__ import annotations

import discord

from bot.cogs.currency import service
from bot.cogs.currency.embeds import CURRENCY_EMOJI, CURRENCY_NAME


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


class ClaimButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Claim daily",
            style=discord.ButtonStyle.green,
            emoji="🎁",
            custom_id="currencypanel:claim",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        new_balance = await service.claim(interaction.guild_id, interaction.user.id)
        if new_balance is None:
            remaining = await service.claim_cooldown_remaining(interaction.guild_id, interaction.user.id)
            await interaction.response.send_message(
                f"❌ You've already claimed today. Try again in **{_fmt_duration(remaining or 0)}**.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"{CURRENCY_EMOJI} You claimed **{service.CLAIM_AMOUNT}** {CURRENCY_NAME}! "
            f"New balance: **{new_balance:,}**.",
            ephemeral=True,
        )


class BalanceButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="My balance",
            style=discord.ButtonStyle.secondary,
            emoji="💰",
            custom_id="currencypanel:balance",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        wallet = await service.get_or_create_wallet(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            f"{CURRENCY_EMOJI} You have **{wallet['balance']:,}** {CURRENCY_NAME}.",
            ephemeral=True,
        )


class CurrencyPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(ClaimButton())
        self.add_item(BalanceButton())

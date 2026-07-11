from __future__ import annotations

import discord
from loguru import logger

from bot.cogs.betting import service
from bot.cogs.betting.embeds import build_market_embed

_RESULT_MESSAGES = {
    "closed": "❌ Betting on this match is no longer open.",
    "insufficient_funds": "❌ You don't have enough FloshCoins for that stake.",
    "invalid_amount": "❌ Stake must be a positive whole number.",
}


async def refresh_market_message(client: discord.Client, market_id: int) -> None:
    """Re-render a market card message in place from the current DB state."""
    market = await service.get_market(market_id)
    if not market or not market["channel_id"] or not market["message_id"]:
        return
    bets = await service.get_bets(market_id)
    channel = client.get_channel(market["channel_id"])
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(market["message_id"])
        view = make_market_view(market) if market["status"] == "open" else None
        await msg.edit(embed=build_market_embed(market, bets), view=view)
    except discord.NotFound:
        pass


def make_market_view(market) -> MarketView:
    return MarketView(market["id"], service.outcomes_for_market(market))


class StakeModal(discord.ui.Modal, title="Place your bet"):
    stake_input = discord.ui.TextInput(label="Stake (FloshCoins)", placeholder="100", required=True, max_length=10)

    def __init__(self, market_id: int, outcome: str, outcome_label: str) -> None:
        super().__init__()
        self.market_id = market_id
        self.outcome = outcome
        self.title = f"Bet on {outcome_label}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = int(str(self.stake_input))
        except ValueError:
            await interaction.response.send_message("❌ Stake must be a whole number.", ephemeral=True)
            return

        result, new_balance = await service.place_bet(
            market_id=self.market_id,
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            outcome=self.outcome,
            amount=amount,
        )
        if result != "ok":
            await interaction.response.send_message(_RESULT_MESSAGES[result], ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Bet placed: **{amount:,}** 🪙 on **{self.outcome}**. New balance: **{new_balance:,}**.",
            ephemeral=True,
        )
        try:
            await refresh_market_message(interaction.client, self.market_id)
        except discord.HTTPException as e:
            logger.warning(f"Failed to refresh market message after bet: {e}")


class OutcomeButton(discord.ui.Button):
    def __init__(self, market_id: int, outcome: str, label: str) -> None:
        super().__init__(
            label=f"Bet {label}",
            style=discord.ButtonStyle.primary,
            custom_id=f"betting:bet:{market_id}:{outcome}",
        )
        self.market_id = market_id
        self.outcome = outcome
        self.outcome_label = label

    async def callback(self, interaction: discord.Interaction) -> None:
        market = await service.get_market(self.market_id)
        if not market or market["status"] != "open":
            await interaction.response.send_message("❌ Betting on this match is no longer open.", ephemeral=True)
            return
        await interaction.response.send_modal(StakeModal(self.market_id, self.outcome, self.outcome_label))


class MarketView(discord.ui.View):
    def __init__(self, market_id: int, outcomes: list[tuple[str, str]]) -> None:
        super().__init__(timeout=None)
        for key, label in outcomes:
            self.add_item(OutcomeButton(market_id, key, label))

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

# Discord component limits.
MODAL_TITLE_LIMIT = 45
BUTTON_LABEL_LIMIT = 80


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


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


async def announce_result(client: discord.Client, market_id: int) -> None:
    """Post a result summary under the market card, so bettors actually learn how they did.

    Without this the card is silently edited in place, far up the channel, and nobody notices.
    Skipped entirely when nobody bet, to avoid spamming the channel with empty results.
    """
    market = await service.get_market(market_id)
    if not market or not market["channel_id"] or not market["message_id"]:
        return
    bets = await service.get_bets(market_id)
    if not bets:
        return

    channel = client.get_channel(market["channel_id"])
    if channel is None:
        return

    labels = dict(service.outcomes_for_market(market))
    headline = (
        market["competition"] if market["sport"] == "custom" else f"{market['home_name']} vs {market['away_name']}"
    )

    if market["status"] == "void":
        text = f"⚠️ **{headline}** was cancelled — all **{len(bets)}** bet(s) refunded."
    else:
        winner_label = labels.get(market["winner"], market["winner"])
        winners = [b for b in bets if (b["payout"] or 0) > 0]
        if not winners:
            text = f"🏁 **{headline}** — **{winner_label}** won, but nobody backed them. No payouts!"
        else:
            lines = [
                f"<@{b['user_id']}> +**{b['payout'] - b['amount']:,}** 🪙 (staked {b['amount']:,})"
                for b in sorted(winners, key=lambda b: b["payout"], reverse=True)[:10]
            ]
            losers = len(bets) - len(winners)
            text = f"🏁 **{headline}** — **{winner_label}** won!\n\n" + "\n".join(lines)
            if losers:
                text += f"\n\n{losers} bet(s) on the losing side."

    try:
        card = await channel.fetch_message(market["message_id"])
        await channel.send(text, reference=card)
    except discord.NotFound:
        await channel.send(text)
    except discord.HTTPException as e:
        logger.warning(f"Failed to announce result for market {market_id}: {e}")


class StakeModal(discord.ui.Modal, title="Place your bet"):
    stake_input = discord.ui.TextInput(label="Stake (FloshCoins)", placeholder="100", required=True, max_length=10)

    def __init__(self, market_id: int, outcome: str, outcome_label: str) -> None:
        super().__init__()
        self.market_id = market_id
        self.outcome = outcome
        self.outcome_label = outcome_label
        # Discord rejects modal titles over 45 characters.
        self.title = _truncate(f"Bet on {outcome_label}", MODAL_TITLE_LIMIT)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = int(str(self.stake_input).strip())
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
            f"✅ Bet placed: **{amount:,}** 🪙 on **{self.outcome_label}**. New balance: **{new_balance:,}**.",
            ephemeral=True,
        )
        try:
            await refresh_market_message(interaction.client, self.market_id)
        except discord.HTTPException as e:
            logger.warning(f"Failed to refresh market message after bet: {e}")


class OutcomeButton(discord.ui.Button):
    def __init__(self, market_id: int, outcome: str, label: str) -> None:
        super().__init__(
            # Discord rejects button labels over 80 characters.
            label=_truncate(f"Bet {label}", BUTTON_LABEL_LIMIT),
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

from __future__ import annotations

from collections.abc import Mapping, Sequence

import discord
from loguru import logger

from bot.cogs.betting import service
from bot.cogs.betting.cards import mark_card_dirty
from bot.cogs.betting.embeds import build_market_embed, build_staff_result_embed
from bot.cogs.currency import service as currency_service
from bot.cogs.currency.leaderboard import mark_dirty

CLOSED_MESSAGE = "❌ Betting has closed on this one — no new stakes. Anything you already staked still stands."

_RESULT_MESSAGES = {
    "closed": CLOSED_MESSAGE,
    # insufficient_funds is handled separately, so the bettor is told their actual balance.
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


async def announce_result_to_staff(client: discord.Client, market_id: int, settled_by: str) -> None:
    """Post the full breakdown of a settled market in the staff channel.

    Not in the betting channel — that one holds cards, and a pinged list of winners under every
    settled fixture was the noise this replaced. And not nowhere: a custom bet's winner is
    declared by a person, so *somebody* has to be able to see who declared what without opening
    psql. That is what this is.

    No staff channel configured means no recap and no error: the DMs still went out and the card
    is still rewritten. Degraded, not broken.

    Skipped when no member bet: the house is staked on every market ever opened, so this would
    otherwise fire under every fixture nobody played.
    """
    market = await service.get_market(market_id)
    if not market:
        return
    channel_id = await service.get_staff_channel(market["guild_id"])
    if not channel_id:
        return

    bets = await service.get_bets(market_id)
    if not service.player_bets(bets):
        return

    channel = client.get_channel(channel_id)
    if channel is None:
        logger.warning(f"Staff channel {channel_id} is not visible — no recap for market {market_id}")
        return

    try:
        await channel.send(embed=build_staff_result_embed(market, bets, settled_by))
    except discord.HTTPException as e:
        logger.warning(f"Failed to post the staff recap for market {market_id}: {e}")


def _headline(market: Mapping) -> str:
    return market["competition"] if market["sport"] == "custom" else f"{market['home_name']} vs {market['away_name']}"


def build_dm_text(market: Mapping, bets: Sequence[Mapping], balance: int) -> str:
    """What one bettor is told about one market. `bets` is that person's rows only.

    Someone who topped up their position has several rows and gets one message, not three.
    """
    staked = sum(b["amount"] for b in bets)
    payout = sum(b["payout"] or 0 for b in bets)
    headline = _headline(market)
    labels = dict(service.outcomes_for_market(market))
    backed = labels.get(bets[0]["outcome"], bets[0]["outcome"])

    if market["status"] == "void":
        return f"⚠️ **{headline}** a été annulé — tes **{staked:,}** 🪙 t'ont été rendus.\nSolde : **{balance:,}** 🪙."

    winner = labels.get(market["winner"], market["winner"])
    if payout > 0:
        return (
            f"🎉 **{headline}** — **{winner}** a gagné !\n"
            f"Tu avais misé **{staked:,}** 🪙 → tu récupères **{payout:,}** 🪙 (**+{payout - staked:,}**).\n"
            f"Nouveau solde : **{balance:,}** 🪙."
        )
    return (
        f"💀 **{headline}** — **{winner}** a gagné.\n"
        f"Tu avais misé **{staked:,}** 🪙 sur **{backed}**. Perdu.\n"
        f"Solde : **{balance:,}** 🪙."
    )


async def dm_bettors(client: discord.Client, market_id: int) -> None:
    """Tell every bettor how they did, in private.

    This used to be a pinged list under the card. A football matchday settles five matches in a
    row, so the one channel that should hold nothing but cards held five walls of pings.

    The house is skipped: it is staked on every market ever opened and does not read its mail.
    Each send is isolated — one member who blocked the bot must not cost the other nine theirs,
    and closed DMs are a *choice*, not an error: the card and /bet mine are still there for them.
    """
    market = await service.get_market(market_id)
    if not market:
        return

    by_user: dict[int, list[Mapping]] = {}
    for bet in service.player_bets(await service.get_bets(market_id)):
        by_user.setdefault(bet["user_id"], []).append(bet)

    for user_id, bets in by_user.items():
        try:
            user = client.get_user(user_id) or await client.fetch_user(user_id)
            balance = await currency_service.get_balance(market["guild_id"], user_id)
            await user.send(build_dm_text(market, bets, balance))
        except discord.Forbidden:
            logger.info(f"Bettor {user_id} has DMs closed — no result DM for market {market_id}")
        except discord.HTTPException as e:
            logger.warning(f"Failed to DM bettor {user_id} about market {market_id}: {e}")


async def remind_to_settle(client: discord.Client, market) -> None:
    """Nudge whoever can settle a stuck market. The point is that frozen coins are never silent.

    Where the nudge goes follows the gavel. A creator who stayed out of their own bet is chased
    under their own card, publicly, because it is theirs to close. A creator who *bet* on it can
    no longer settle it — pinging them would be telling them to do something the bot will
    refuse — so the chase goes to the staff channel, along with every provider match whose API
    never reported a result. Falls back to the betting channel when there is no staff channel:
    a frozen market must never be silent.

    Only when members' money is actually stuck: the house's seed doesn't count (it is on every
    market ever opened), and nagging about a bet nobody staked on would just be noise.
    """
    all_bets = await service.get_bets(market["id"])
    bets = service.player_bets(all_bets)
    if not bets:
        return

    staked = sum(b["amount"] for b in bets)
    backers = len({b["user_id"] for b in bets})
    stuck = f"**{staked:,}** 🪙 de **{backers}** personne(s) sont bloqués."

    if service.creator_holds_gavel(market, all_bets):
        text = (
            f"⏰ <@{market['creator_user_id']}> — ton pari **{market['competition']}** est fermé "
            f"mais pas encore clôturé.\n"
            f"{stuck}\n"
            f"Utilise `/bet resolve` pour désigner le gagnant, ou `/bet cancel` pour tout rembourser."
        )
        channel = client.get_channel(market["channel_id"]) if market["channel_id"] else None
    else:
        if market["provider"] == "custom":
            text = (
                f"⏰ Le pari **{market['competition']}** de <@{market['creator_user_id']}> est fermé "
                f"et attend un arbitre.\n"
                f"Son créateur a misé dessus, c'est donc à un modérateur de trancher.\n"
                f"{stuck}\n"
                f"`/bet resolve` pour désigner le gagnant, `/bet cancel` pour rembourser."
            )
        else:
            days = service.STUCK_VOID_AFTER.days
            text = (
                f"⏰ **{market['home_name']} vs {market['away_name']}** est fermé, "
                f"mais le résultat n'est jamais arrivé.\n"
                f"{stuck}\n"
                f"Un modérateur peut le régler avec `/bet resolve`, ou `/bet cancel` pour rembourser. "
                f"Remboursement automatique {days} jours après le coup d'envoi."
            )
        staff_id = await service.get_staff_channel(market["guild_id"])
        channel = client.get_channel(staff_id) if staff_id else None
        # No staff channel: better noisy in the betting channel than silent about frozen coins.
        if channel is None and market["channel_id"]:
            channel = client.get_channel(market["channel_id"])

    if channel is None:
        return

    # A reference is only worth attempting when the reminder lands in the card's own channel
    # — in the staff channel it is a guaranteed NotFound (wasted round-trip) and, worse, a
    # Forbidden (no Read Message History there) would be swallowed by the except below and
    # leave the reminder unsent, which is the exact silence this function exists to prevent.
    if market["message_id"] and channel.id == market["channel_id"]:
        try:
            card = await channel.fetch_message(market["message_id"])
            await channel.send(text, reference=card)
            return
        except discord.NotFound:
            pass  # card was deleted from its own channel — fall through and send plainly
        except discord.HTTPException as e:
            logger.warning(f"Failed to send settle reminder for market {market['id']}: {e}")
            return

    try:
        await channel.send(text)
    except discord.HTTPException as e:
        logger.warning(f"Failed to send settle reminder for market {market['id']}: {e}")


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

        result = await service.place_bet(
            market_id=self.market_id,
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            outcome=self.outcome,
            amount=amount,
        )

        if result.status == "other_outcome":
            market = await service.get_market(self.market_id)
            backed = dict(service.outcomes_for_market(market)).get(result.existing_outcome, "another option")
            await interaction.response.send_message(
                f"❌ You're already on **{backed}** for this one — you can only back one option.\n"
                f"You can add more to **{backed}**, but not switch or bet both sides.",
                ephemeral=True,
            )
            return
        if result.status == "insufficient_funds":
            await interaction.response.send_message(
                f"❌ You only have **{result.new_balance:,}** 🪙 — not enough for a **{amount:,}** 🪙 stake.\n"
                f"Use `/claim` for your daily coins.",
                ephemeral=True,
            )
            return
        if result.status != "ok":
            await interaction.response.send_message(
                _RESULT_MESSAGES.get(result.status, "❌ That bet couldn't be placed."), ephemeral=True
            )
            return

        mark_dirty(interaction.client)  # the stake was just debited

        # The stake is already in the pool, so the current odds are what it would pay today.
        bets = await service.get_bets(self.market_id)
        odds = service.implied_odds(bets).get(self.outcome, 1.0)
        my_stake = sum(b["amount"] for b in bets if b["user_id"] == interaction.user.id)

        verb = "Added to your bet" if result.topped_up else "Bet placed"
        stake_line = f"✅ {verb}: **{amount:,}** 🪙 on **{self.outcome_label}**" + (
            f" (now **{my_stake:,}** 🪙 total)." if result.topped_up else "."
        )
        await interaction.response.send_message(
            f"{stake_line}\n"
            f"Returns **{int(my_stake * odds):,}** 🪙 if it wins (`{odds:.2f}x`, at current odds).\n"
            f"New balance: **{result.new_balance:,}** 🪙.",
            ephemeral=True,
        )
        # Batched rather than edited here: twenty people betting at once should cost one
        # card edit, not twenty. The cog redraws it within a few seconds.
        mark_card_dirty(interaction.client, self.market_id)


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
            await interaction.response.send_message(CLOSED_MESSAGE, ephemeral=True)
            return
        await interaction.response.send_modal(StakeModal(self.market_id, self.outcome, self.outcome_label))


class MarketView(discord.ui.View):
    def __init__(self, market_id: int, outcomes: list[tuple[str, str]]) -> None:
        super().__init__(timeout=None)
        for key, label in outcomes:
            self.add_item(OutcomeButton(market_id, key, label))

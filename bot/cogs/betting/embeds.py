from __future__ import annotations

from collections.abc import Mapping, Sequence

import discord

from bot.cogs.betting.service import implied_odds, outcomes_for_market, pool_shares, pool_totals

BAR_WIDTH = 12

OUTCOME_EMOJI = {"home": "🏠", "draw": "🤝", "away": "🛫"}
CUSTOM_OUTCOME_EMOJI = {"home": "🅰️", "away": "🅱️"}
SPORT_EMOJI = {"football": "⚽", "lol": "🎮", "custom": "🎲"}


def _is_custom(market: Mapping) -> bool:
    return market["sport"] == "custom"


def _bar(share: float) -> str:
    """A Twitch-style filled bar showing this outcome's share of the pool."""
    filled = round(share * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def build_market_embed(market: Mapping, bets: Sequence[Mapping]) -> discord.Embed:
    status = market["status"]
    custom = _is_custom(market)
    totals = pool_totals(bets)
    total_pool = sum(t["total"] for t in totals.values())
    ts = int(market["start_time"].timestamp())
    emoji = CUSTOM_OUTCOME_EMOJI if custom else OUTCOME_EMOJI

    odds = implied_odds(bets)
    shares = pool_shares(bets)
    lines = []
    for key, label in outcomes_for_market(market):
        entry = totals.get(key, {"total": 0, "count": 0, "backers": 0})
        # An outcome nobody has backed has no meaningful cote yet — don't invent one.
        cote = f"`{odds[key]:.2f}x`" if key in odds else "`—`"
        winner_mark = " ✅" if status == "resolved" and key == market["winner"] else ""
        share = shares.get(key, 0.0)
        people = entry["backers"]
        lines.append(
            f"{emoji[key]} **{label}** {cote}{winner_mark}\n"
            f"`{_bar(share)}` {share:.0%} · {entry['total']:,} 🪙 · {people} 👤"
        )

    if custom:
        # For a custom bet the competition column holds the question being bet on.
        matchup = market["competition"]
        closing_word, closed_word, void_word = "Closes", "Betting closed — awaiting result", "Bet cancelled"
    else:
        matchup = f"**{market['home_name']}** vs **{market['away_name']}**"
        closing_word, closed_word, void_word = "Kickoff", "Betting closed — match in progress", "Match voided"

    if status == "open":
        title = f"{SPORT_EMOJI[market['sport']]} {market['competition'] if not custom else 'Community bet'}"
        color = discord.Color.blurple()
        description = f"{matchup}\n\n{closing_word}: <t:{ts}:R>"
        # Parimutuel odds are indicative: they move as money comes in, and you're paid the
        # odds at settlement, not the odds you saw when you bet. Say so, or it looks like a bug.
        footer = "One option each · odds shift as bets come in — you're paid the final odds"
    elif status == "locked":
        title = f"🔒 {market['competition'] if not custom else 'Community bet'}"
        color = discord.Color.greyple()
        description = matchup
        footer = closed_word
    elif status == "resolved":
        winner = market["winner"]
        winner_label = dict(outcomes_for_market(market)).get(winner, winner)
        title = f"✅ {'Community bet' if custom else market['competition']} — {winner_label} won"
        color = discord.Color.green()
        description = matchup
        winning_pool = totals.get(winner, {"total": 0})["total"]
        if winning_pool > 0:
            footer = f"Payout: {total_pool / winning_pool:.2f}x stake"
        else:
            footer = "Nobody bet on the winning outcome — no payouts"
    else:  # void
        title = f"⚠️ {'Community bet' if custom else market['competition']} — {void_word}"
        color = discord.Color.red()
        description = matchup
        footer = "All bets refunded"

    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(name="Cotes", value="\n".join(lines), inline=False)
    if custom and market.get("creator_user_id"):
        embed.add_field(name="Opened by", value=f"<@{market['creator_user_id']}>", inline=False)
    embed.set_footer(text=f"{footer} · Total pool: {total_pool:,} 🪙")
    return embed

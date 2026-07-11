from __future__ import annotations

from collections.abc import Mapping, Sequence

import discord

from bot.cogs.betting.service import outcomes_for_market, pool_totals

OUTCOME_EMOJI = {"home": "🏠", "draw": "🤝", "away": "🛫"}


def _sport_emoji(sport: str) -> str:
    return "⚽" if sport == "football" else "🎮"


def build_market_embed(market: Mapping, bets: Sequence[Mapping]) -> discord.Embed:
    status = market["status"]
    totals = pool_totals(bets)
    total_pool = sum(t["total"] for t in totals.values())
    ts = int(market["start_time"].timestamp())

    lines = []
    for key, label in outcomes_for_market(market):
        entry = totals.get(key, {"total": 0, "count": 0})
        lines.append(f"{OUTCOME_EMOJI[key]} **{label}** — {entry['total']:,} 🪙 ({entry['count']} bets)")

    if status == "open":
        title = f"{_sport_emoji(market['sport'])} {market['competition']}"
        color = discord.Color.blurple()
        description = f"**{market['home_name']}** vs **{market['away_name']}**\n\nKickoff: <t:{ts}:R>"
        footer = "Betting closes at kickoff"
    elif status == "locked":
        title = f"🔒 {market['competition']}"
        color = discord.Color.greyple()
        description = f"**{market['home_name']}** vs **{market['away_name']}**"
        footer = "Betting closed — match in progress"
    elif status == "resolved":
        winner = market["winner"]
        winner_label = dict(outcomes_for_market(market)).get(winner, winner)
        title = f"✅ {market['competition']} — {winner_label} won"
        color = discord.Color.green()
        description = f"**{market['home_name']}** vs **{market['away_name']}**"
        winning_pool = totals.get(winner, {"total": 0})["total"]
        if winning_pool > 0:
            multiplier = total_pool / winning_pool
            footer = f"Payout: {multiplier:.2f}x stake"
        else:
            footer = "Nobody bet on the winning outcome — no payouts"
    else:  # void
        title = f"⚠️ {market['competition']} — Match voided"
        color = discord.Color.red()
        description = f"**{market['home_name']}** vs **{market['away_name']}**"
        footer = "Match postponed or cancelled — all bets refunded"

    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(name="Pool", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"{footer} · Total pool: {total_pool:,} 🪙")
    return embed

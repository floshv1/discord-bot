from __future__ import annotations

from collections.abc import Mapping, Sequence

import discord

CURRENCY_NAME = "FloshCoins"
CURRENCY_EMOJI = "🪙"


def build_panel_embed() -> discord.Embed:
    """The persistent control-panel message posted in the currency channel."""
    return discord.Embed(
        title=f"{CURRENCY_EMOJI} {CURRENCY_NAME}",
        description=(
            f"Everyone starts with **1,000** {CURRENCY_NAME}.\n\n"
            "**•** Tap 🎁 **Claim daily** for **100** more — once a day, resets at midnight.\n"
            "**•** Tap 💰 **My balance** to check your wallet.\n"
            "**•** Spend them betting on matches — winners split the losers' pool."
        ),
        color=discord.Color.gold(),
    )


def build_leaderboard_embed(rows: Sequence[Mapping], names: Mapping[int, str], updated: str) -> discord.Embed:
    embed = discord.Embed(title=f"{CURRENCY_EMOJI} {CURRENCY_NAME} Leaderboard", color=discord.Color.gold())
    embed.set_footer(text=f"Updated {updated}")
    if not rows:
        embed.description = "No wallets yet."
        return embed
    lines = [
        f"**#{i}** {names.get(r['user_id'], f'<@{r["user_id"]}>')} — {r['balance']:,} {CURRENCY_EMOJI}"
        for i, r in enumerate(rows, 1)
    ]
    embed.description = "\n".join(lines)
    return embed

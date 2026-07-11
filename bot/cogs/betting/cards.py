from __future__ import annotations

import discord


def mark_card_dirty(client: discord.Client, market_id: int) -> None:
    """Flag a market card for redraw on the cog's next tick.

    Lives in its own module so the views can call it without importing the cog (which
    imports the views). Mirrors ``bot.cogs.currency.leaderboard.mark_dirty``.
    """
    cog = client.get_cog("BettingCog")
    if cog is not None:
        cog.mark_card_dirty(market_id)

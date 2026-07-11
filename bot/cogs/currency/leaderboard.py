from __future__ import annotations

import discord


def mark_dirty(client: discord.Client) -> None:
    """Flag the currency leaderboard as stale after a balance change.

    The cog's ticker redraws it on the next tick. This is a flag rather than an immediate
    edit so that a flurry of bets settling at once results in one message edit, not twenty.
    Lives in its own module so both the currency views and the betting cog can call it
    without importing the cog (which imports the views).
    """
    cog = client.get_cog("CurrencyCog")
    if cog is not None:
        cog.mark_leaderboard_dirty()

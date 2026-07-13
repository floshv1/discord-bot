from __future__ import annotations

from discord.ext import commands

from bot.cogs.tribunal.views import PleaButton, VerdictButton


class TribunalCog(commands.Cog):
    """The tribunal owns no commands and no clock.

    Trials are opened by /reprimand and closed by the reprimand ticker or a verdict, so the
    only thing to do at boot is teach the bot how to route clicks on cards it has never seen
    — which is exactly what a DynamicItem registration is. One registration, every trial,
    forever; an add_view per row would not survive a restart mid-trial.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(PleaButton, VerdictButton)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TribunalCog(bot))

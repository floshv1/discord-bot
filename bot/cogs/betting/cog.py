from __future__ import annotations

from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.betting import service
from bot.cogs.betting.embeds import build_market_embed
from bot.cogs.betting.providers import Provider
from bot.cogs.betting.providers.football_data import FootballDataProvider
from bot.cogs.betting.providers.pandascore import PandaScoreProvider
from bot.cogs.betting.views import MarketView, refresh_market_message

FIXTURE_LOOKAHEAD_DAYS = 7


class BettingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        config = bot.config  # type: ignore[attr-defined]
        self.providers: list[Provider] = []
        if config.football_data_api_key:
            self.providers.append(FootballDataProvider(config.football_data_api_key))
        if config.pandascore_api_key:
            self.providers.append(PandaScoreProvider(config.pandascore_api_key))

    async def cog_load(self) -> None:
        await self._reregister_open_views()
        if self.providers and self.bot.config.betting_channel_id:  # type: ignore[attr-defined]
            self.fixture_poll_ticker.start()
        self.lock_ticker.start()
        self.resolution_ticker.start()

    async def cog_unload(self) -> None:
        if self.fixture_poll_ticker.is_running():
            self.fixture_poll_ticker.cancel()
        self.lock_ticker.cancel()
        self.resolution_ticker.cancel()

    async def _reregister_open_views(self) -> None:
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        for market in await service.get_open_markets(guild_id):
            if market["message_id"]:
                view = MarketView(market["id"], service.outcomes_for_market(market))
                self.bot.add_view(view)

    def _provider_for(self, name: str) -> Provider | None:
        return next((p for p in self.providers if p.name == name), None)

    # -----------------------------------------------------------------
    # Fixture polling — create new markets from upcoming provider fixtures
    # -----------------------------------------------------------------

    @tasks.loop(hours=6)
    async def fixture_poll_ticker(self) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        guild = self.bot.get_guild(config.guild_id)
        if not guild:
            return
        channel = guild.get_channel(config.betting_channel_id)
        if not channel:
            return

        for provider in self.providers:
            fixtures = await provider.list_upcoming(FIXTURE_LOOKAHEAD_DAYS)
            for fixture in fixtures:
                market_id = await service.create_market(
                    guild_id=guild.id,
                    provider=provider.name,
                    external_id=fixture.external_id,
                    sport=fixture.sport,
                    competition=fixture.competition,
                    home_name=fixture.home_name,
                    away_name=fixture.away_name,
                    start_time=fixture.start_time,
                )
                if market_id is None:
                    continue  # already exists

                market = await service.get_market(market_id)
                view = MarketView(market_id, service.outcomes_for_market(market))
                card = await channel.send(embed=build_market_embed(market, []), view=view)
                await service.set_market_message(market_id, channel.id, card.id)
                self.bot.add_view(view)

    @fixture_poll_ticker.before_loop
    async def before_fixture_poll_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @fixture_poll_ticker.error
    async def fixture_poll_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"fixture_poll_ticker error (will retry next tick): {error}")

    # -----------------------------------------------------------------
    # Locking — close betting once kickoff has passed
    # -----------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def lock_ticker(self) -> None:
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        locked = await service.lock_due_markets(guild_id)
        for market in locked:
            await refresh_market_message(self.bot, market["id"])

    @lock_ticker.before_loop
    async def before_lock_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @lock_ticker.error
    async def lock_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"lock_ticker error (will retry next tick): {error}")

    # -----------------------------------------------------------------
    # Resolution — settle locked markets once the provider reports a result
    # -----------------------------------------------------------------

    @tasks.loop(minutes=5)
    async def resolution_ticker(self) -> None:
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        for market in await service.get_locked_markets(guild_id):
            provider = self._provider_for(market["provider"])
            if provider is None:
                continue
            result = await provider.get_result(market["external_id"])
            if result is None:
                continue  # not finished yet, retry next tick

            if result.status == "finished":
                if result.winner is None:
                    logger.warning(f"Market {market['id']} finished with no winner reported, will retry")
                    continue
                await service.resolve_market(market["id"], result.winner)
            else:
                await service.void_market(market["id"])
            await refresh_market_message(self.bot, market["id"])

    @resolution_ticker.before_loop
    async def before_resolution_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @resolution_ticker.error
    async def resolution_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"resolution_ticker error (will retry next tick): {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BettingCog(bot))

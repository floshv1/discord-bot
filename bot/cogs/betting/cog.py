from __future__ import annotations

import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.betting import service
from bot.cogs.betting.embeds import build_market_embed
from bot.cogs.betting.providers import Provider
from bot.cogs.betting.providers.football_data import FootballDataProvider
from bot.cogs.betting.providers.pandascore import PandaScoreProvider
from bot.cogs.betting.views import MarketView, announce_result, refresh_market_message
from bot.cogs.currency.leaderboard import mark_dirty

FIXTURE_LOOKAHEAD_DAYS = 7
MAX_CUSTOM_BET_HOURS = 24 * 14  # two weeks
MAX_OPTION_LEN = 30  # fits Discord's 45-char modal title once "Bet on " is prepended
MAX_QUESTION_LEN = 100  # fits the autocomplete choice-name limit


def _market_label(market) -> str:
    if market["sport"] == "custom":
        return market["competition"]
    return f"{market['home_name']} vs {market['away_name']}"


async def _settleable_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    markets = await service.list_settleable_markets(interaction.guild_id)
    is_mod = interaction.user.guild_permissions.manage_messages
    current = current.lower()

    choices = []
    for m in markets:
        # Members only ever settle their own community bets; mods can settle anything,
        # including a real match left stuck because a provider never reported a result.
        if not is_mod and (m["provider"] != "custom" or m["creator_user_id"] != interaction.user.id):
            continue
        label = _market_label(m)
        if current not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=str(m["id"])))
    return choices[:25]


async def _winner_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Offer the selected bet's own outcome labels, so the settler picks a real name not 'Option A'."""
    raw = interaction.namespace.bet
    if not raw:
        return []
    try:
        market = await service.get_market(int(raw))
    except (TypeError, ValueError):
        return []
    if not market:
        return []
    current = current.lower()
    return [
        app_commands.Choice(name=label[:100], value=key)
        for key, label in service.outcomes_for_market(market)
        if current in label.lower()
    ]


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
        if self.providers:
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
    # Custom, user-created bets
    # -----------------------------------------------------------------

    bet = app_commands.Group(name="bet", description="Open and settle community bets.")

    @bet.command(name="create", description="Open a community bet with two outcomes.")
    @app_commands.describe(
        question="What is being bet on, e.g. 'Who wins tonight's scrim?'",
        option_a="First outcome, e.g. 'Team Blue'",
        option_b="Second outcome, e.g. 'Team Red'",
        closes_in_hours="Hours until betting closes (1-336)",
    )
    async def bet_create(
        self,
        interaction: discord.Interaction,
        question: str,
        option_a: str,
        option_b: str,
        closes_in_hours: int,
    ) -> None:
        question, option_a, option_b = question.strip(), option_a.strip(), option_b.strip()
        if not 1 <= closes_in_hours <= MAX_CUSTOM_BET_HOURS:
            await interaction.response.send_message(
                f"❌ Betting window must be between 1 and {MAX_CUSTOM_BET_HOURS} hours.", ephemeral=True
            )
            return
        if option_a.lower() == option_b.lower():
            await interaction.response.send_message("❌ The two outcomes must be different.", ephemeral=True)
            return
        # Keeps the outcome names inside Discord's button-label and modal-title limits.
        if len(option_a) > MAX_OPTION_LEN or len(option_b) > MAX_OPTION_LEN:
            await interaction.response.send_message(
                f"❌ Each outcome must be {MAX_OPTION_LEN} characters or fewer.", ephemeral=True
            )
            return
        if len(question) > MAX_QUESTION_LEN:
            await interaction.response.send_message(
                f"❌ The question must be {MAX_QUESTION_LEN} characters or fewer.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Post into the betting channel if one is configured, otherwise right here.
        channel_id = await service.get_betting_channel(interaction.guild_id)
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        channel = channel or interaction.channel

        closes_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=closes_in_hours)
        market_id = await service.create_custom_market(
            guild_id=interaction.guild_id,
            creator_user_id=interaction.user.id,
            title=question,
            option_a=option_a,
            option_b=option_b,
            closes_at=closes_at,
        )

        market = await service.get_market(market_id)
        view = MarketView(market_id, service.outcomes_for_market(market))
        card = await channel.send(embed=build_market_embed(market, []), view=view)
        await service.set_market_message(market_id, channel.id, card.id)
        self.bot.add_view(view)
        await interaction.followup.send(f"✅ Bet opened in {channel.mention}!", ephemeral=True)

    @bet.command(name="mine", description="See the bets you currently have riding.")
    async def bet_mine(self, interaction: discord.Interaction) -> None:
        rows = await service.get_active_bets_for_user(interaction.guild_id, interaction.user.id)
        if not rows:
            await interaction.response.send_message("You have no open bets right now.", ephemeral=True)
            return

        staked = sum(r["amount"] for r in rows)
        lines = []
        for r in rows:
            label = dict(service.outcomes_for_market(r))[r["outcome"]]
            lock = "🔒" if r["status"] == "locked" else "🟢"
            lines.append(f"{lock} **{_market_label(r)}** — {r['amount']:,} 🪙 on **{label}**")

        embed = discord.Embed(
            title="🎲 Your open bets",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{staked:,} 🪙 staked across {len(rows)} bet(s) · 🔒 = betting closed")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bet.command(name="resolve", description="Declare the winning outcome of a community bet and pay out.")
    @app_commands.describe(bet="The bet to settle", winner="Which outcome won")
    @app_commands.autocomplete(bet=_settleable_autocomplete, winner=_winner_autocomplete)
    async def bet_resolve(self, interaction: discord.Interaction, bet: str, winner: str) -> None:
        market = await self._settleable_or_error(interaction, bet)
        if market is None:
            return
        if winner not in dict(service.outcomes_for_market(market)):
            await interaction.response.send_message("❌ Pick a winning outcome from the list.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await service.resolve_market(market["id"], winner)
        mark_dirty(self.bot)
        await refresh_market_message(self.bot, market["id"])
        await announce_result(self.bot, market["id"])
        winner_label = dict(service.outcomes_for_market(market))[winner]
        await interaction.followup.send(f"✅ Settled — **{winner_label}** won. Payouts sent.", ephemeral=True)

    @bet.command(name="cancel", description="Cancel a community bet and refund every stake.")
    @app_commands.describe(bet="The bet to cancel")
    @app_commands.autocomplete(bet=_settleable_autocomplete)
    async def bet_cancel(self, interaction: discord.Interaction, bet: str) -> None:
        market = await self._settleable_or_error(interaction, bet)
        if market is None:
            return

        await interaction.response.defer(ephemeral=True)
        await service.void_market(market["id"])
        mark_dirty(self.bot)
        await refresh_market_message(self.bot, market["id"])
        await announce_result(self.bot, market["id"])
        await interaction.followup.send("✅ Bet cancelled — all stakes refunded.", ephemeral=True)

    async def _settleable_or_error(self, interaction: discord.Interaction, bet: str):
        """Resolve the autocomplete value to a market the caller is allowed to settle."""
        try:
            market_id = int(bet)
        except ValueError:
            await interaction.response.send_message("❌ Pick a bet from the list.", ephemeral=True)
            return None

        market = await service.get_market(market_id)
        if not market or market["status"] not in ("open", "locked"):
            await interaction.response.send_message("❌ That bet is not open for settling.", ephemeral=True)
            return None

        is_mod = interaction.user.guild_permissions.manage_messages
        is_owner = market["provider"] == "custom" and market["creator_user_id"] == interaction.user.id
        if not (is_mod or is_owner):
            await interaction.response.send_message(
                "❌ Only the person who opened this bet (or a moderator) can settle it.", ephemeral=True
            )
            return None
        return market

    # -----------------------------------------------------------------
    # Fixture polling — create new markets from upcoming provider fixtures
    # -----------------------------------------------------------------

    async def poll_fixtures_now(self) -> dict[str, int]:
        """Post a card for every new fixture from every provider. Returns {provider: markets_created}.

        Each provider and each fixture is isolated: one failing provider (or one malformed
        fixture) must not stop the others from being posted.
        """
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        created = {p.name: 0 for p in self.providers}

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return created
        channel_id = await service.get_betting_channel(guild_id)
        if not channel_id:
            return created  # /setup betting hasn't been run yet
        channel = guild.get_channel(channel_id)
        if not channel:
            return created

        for provider in self.providers:
            try:
                fixtures = await provider.list_upcoming(FIXTURE_LOOKAHEAD_DAYS)
            except Exception:
                logger.exception(f"Provider {provider.name} failed to list fixtures; skipping it this poll")
                continue

            for fixture in fixtures:
                try:
                    if await self._post_fixture(channel, provider.name, fixture):
                        created[provider.name] += 1
                except Exception:
                    logger.exception(f"Failed to post fixture {provider.name}:{fixture.external_id}; skipping it")
        return created

    async def _post_fixture(self, channel, provider_name: str, fixture) -> bool:
        """Create and post one market. Returns False if it already existed."""
        market_id = await service.create_market(
            guild_id=channel.guild.id,
            provider=provider_name,
            external_id=fixture.external_id,
            sport=fixture.sport,
            competition=fixture.competition,
            home_name=fixture.home_name,
            away_name=fixture.away_name,
            start_time=fixture.start_time,
        )
        if market_id is None:
            return False

        market = await service.get_market(market_id)
        view = MarketView(market_id, service.outcomes_for_market(market))
        card = await channel.send(embed=build_market_embed(market, []), view=view)
        await service.set_market_message(market_id, channel.id, card.id)
        self.bot.add_view(view)
        return True

    @tasks.loop(hours=6)
    async def fixture_poll_ticker(self) -> None:
        await self.poll_fixtures_now()

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
            # Custom bets have no provider and are settled by hand — skip them here.
            provider = self._provider_for(market["provider"])
            if provider is None:
                continue
            # One market failing to settle must not block every other market behind it.
            try:
                await self._settle_from_provider(provider, market)
            except Exception:
                logger.exception(f"Failed to settle market {market['id']}; will retry next tick")

    async def _settle_from_provider(self, provider: Provider, market) -> None:
        result = await provider.get_result(market["external_id"])
        if result is None:
            return  # not finished yet, retry next tick

        if result.status == "finished":
            if result.winner is None:
                logger.warning(f"Market {market['id']} finished with no winner reported, will retry")
                return
            await service.resolve_market(market["id"], result.winner)
        else:
            await service.void_market(market["id"])
        mark_dirty(self.bot)
        await refresh_market_message(self.bot, market["id"])
        await announce_result(self.bot, market["id"])

    @resolution_ticker.before_loop
    async def before_resolution_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @resolution_ticker.error
    async def resolution_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"resolution_ticker error (will retry next tick): {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BettingCog(bot))

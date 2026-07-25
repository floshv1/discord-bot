from __future__ import annotations

import datetime
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.betting import service
from bot.cogs.betting.embeds import build_board_embed, build_market_embed
from bot.cogs.betting.providers import Provider, ResultDTO
from bot.cogs.betting.providers.football_data import FootballDataProvider
from bot.cogs.betting.providers.pandascore import PandaScoreProvider
from bot.cogs.betting.service import CREATE_FEE
from bot.cogs.betting.views import (
    MarketView,
    announce_result_to_staff,
    dm_bettors,
    refresh_market_message,
    remind_to_settle,
)
from bot.cogs.currency.leaderboard import mark_dirty
from bot.core.discord_utils import pin

FIXTURE_LOOKAHEAD_DAYS = 7
MAX_CUSTOM_BET_HOURS = 24 * 14  # two weeks
MAX_OPTION_LEN = 30  # fits Discord's 45-char modal title once "Bet on " is prepended
MAX_QUESTION_LEN = 100  # fits the autocomplete choice-name limit


@dataclass
class PollStats:
    """Outcome of polling one provider. `existing` matters: it's how we tell 'already posted'
    apart from 'the feed is broken', which otherwise both show up as zero new markets."""

    created: int = 0
    existing: int = 0
    restored: int = 0
    failed: bool = False

    def summary(self) -> str:
        if self.failed:
            return "⚠️ request failed — see logs"
        bits = []
        if self.created:
            bits.append(f"**{self.created}** new match(es) posted")
        if self.restored:
            bits.append(f"**{self.restored}** re-posted (card was missing)")
        if bits:
            if self.existing:
                bits.append(f"{self.existing} already up")
            return ", ".join(bits)
        if self.existing:
            return f"up to date (**{self.existing}** already posted)"
        return "no upcoming fixtures found"


def _market_label(market) -> str:
    if market["sport"] == "custom":
        return market["competition"]
    return f"{market['home_name']} vs {market['away_name']}"


async def _settleable_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    markets = await service.list_settleable_markets(
        interaction.guild_id,
        interaction.user.id,
        is_mod=interaction.user.guild_permissions.manage_messages,
    )
    current = current.lower()
    return [
        app_commands.Choice(name=_market_label(m)[:100], value=str(m["id"]))
        for m in markets
        if current in _market_label(m).lower()
    ][:25]


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
        self._dirty_cards: set[int] = set()

    def mark_card_dirty(self, market_id: int) -> None:
        """Flag a market card as stale. The ticker redraws it on the next tick.

        Same trick as the currency leaderboard: editing the card on every single bet means
        a popular market takes one Discord edit per bet, which trips the rate limit. Batching
        collapses a flurry into one edit per card, at the cost of a few seconds of staleness.
        """
        self._dirty_cards.add(market_id)

    async def cog_load(self) -> None:
        await self._purge_retired_leagues()
        await self._reregister_open_views()
        await self._seed_unseeded_markets()
        # Say which providers are live, out loud. A provider with no key is skipped in __init__
        # without a word, and a provider that *is* working also says nothing — so silence in the
        # logs used to mean both "football is fine" and "there is no football at all". The only
        # way to tell them apart was to go looking for cards. Same silent-skip shape as a league
        # name that matches nothing: if it can be absent, it has to announce itself.
        if self.providers:
            logger.info(f"Betting providers loaded: {', '.join(p.name for p in self.providers)}")
            self.fixture_poll_ticker.start()
        else:
            logger.warning(
                "No betting provider configured (FOOTBALL_DATA_API_KEY / PANDASCORE_API_KEY are "
                "both unset) — no fixtures will be polled; only /bet create will work"
            )
        self.lock_ticker.start()
        self.resolution_ticker.start()
        self.card_refresh_ticker.start()

    async def _purge_retired_leagues(self) -> None:
        """Void and delete any market left over from a league we no longer follow.

        Dropping a league from pandascore.LEAGUES / service._LOL_CATEGORIES stops *new* markets,
        but ones already in betting_markets keep pooling into CATEGORY_INTER via the fallback in
        category_for — so their cards land in the inter channel and their fixtures show on its
        board. Void refunds every stake (ledger-safe, via the same path as _void_stuck), then the
        card is deleted so the market is gone for good. Idempotent: a voided market no longer
        matches, so re-runs at boot are no-ops.
        """
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        for market in await service.get_unsettled_markets_for_competitions(guild_id, service.RETIRED_LEAGUES):
            try:
                if not (await service.void_market(market["id"])).voided:
                    continue  # already settled by a racing ticker — it sent the DMs/recap
                logger.warning(
                    f"Purged retired-league market {market['id']} ({_market_label(market)}) — refunded every stake"
                )
                mark_dirty(self.bot)
                await dm_bettors(self.bot, market["id"])
                await announce_result_to_staff(self.bot, market["id"], settled_by="retrait des ligues LCK/LFL")
                await self._delete_card(market)
            except Exception:
                logger.exception(f"Failed to purge retired-league market {market['id']}")

    async def _delete_card(self, market) -> None:
        """Best-effort deletion of a market's card message. The market is voided regardless."""
        if not (market["channel_id"] and market["message_id"]):
            return
        channel = self.bot.get_channel(market["channel_id"])
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(market["message_id"])
            await msg.delete()
        except discord.HTTPException:
            pass  # already gone, or Manage Messages missing — nothing else to do

    async def _seed_unseeded_markets(self) -> None:
        """Give the markets already on the board a house line too.

        Otherwise the cards posted before this shipped would keep showing the degenerate odds
        that started all this, for up to a week, sitting next to freshly seeded ones. Only
        `open` markets are touched — seed_market refuses a locked one, whose bettors were
        promised the pool they could see.
        """
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        for market in await service.get_open_markets(guild_id):
            if await service.seed_market(market["id"]):
                self.mark_card_dirty(market["id"])

    async def cog_unload(self) -> None:
        if self.fixture_poll_ticker.is_running():
            self.fixture_poll_ticker.cancel()
        self.lock_ticker.cancel()
        self.resolution_ticker.cancel()
        self.card_refresh_ticker.cancel()

    @tasks.loop(seconds=5)
    async def card_refresh_ticker(self) -> None:
        # Drain first: a bet placed while we're redrawing must dirty the card again, not
        # get swallowed by the refresh that's already in flight.
        dirty, self._dirty_cards = self._dirty_cards, set()
        for market_id in dirty:
            await refresh_market_message(self.bot, market_id)

    @card_refresh_ticker.before_loop
    async def before_card_refresh_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @card_refresh_ticker.error
    async def card_refresh_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"card_refresh_ticker error (will retry next tick): {error}")

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

    @bet.command(name="create", description=f"Open a community bet with two outcomes (costs {CREATE_FEE} coins).")
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

        # Charge before creating anything, so a failed payment can't leave an orphan card.
        charged, balance = await service.charge_creation_fee(guild_id=interaction.guild_id, user_id=interaction.user.id)
        if not charged:
            await interaction.followup.send(
                f"❌ Opening a bet costs **{service.CREATE_FEE:,}** 🪙 and you have **{balance:,}** 🪙.\n"
                f"Use `/claim` for your daily coins.",
                ephemeral=True,
            )
            return
        mark_dirty(self.bot)  # the fee was just debited

        # Post into the community-bet channel if one is configured (falling back to the one
        # betting channel), otherwise right here.
        channel_id = await service.get_channel_for_category(interaction.guild_id, service.CATEGORY_CUSTOM)
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

        await service.seed_market(market_id)
        market = await service.get_market(market_id)
        bets = await service.get_bets(market_id)
        view = MarketView(market_id, service.outcomes_for_market(market))
        card = await channel.send(embed=build_market_embed(market, bets), view=view)
        await service.set_market_message(market_id, channel.id, card.id)
        self.bot.add_view(view)
        await interaction.followup.send(
            f"✅ Bet opened in {channel.mention}! "
            f"That cost **{service.CREATE_FEE:,}** 🪙 — you have **{balance:,}** 🪙 left.\n"
            f"Remember to settle it with `/bet resolve` once you know the answer.",
            ephemeral=True,
        )

    @bet.command(name="mine", description="See the bets you currently have riding.")
    async def bet_mine(self, interaction: discord.Interaction) -> None:
        rows = await service.get_active_bets_for_user(interaction.guild_id, interaction.user.id)
        if not rows:
            await interaction.response.send_message("You have no open bets right now.", ephemeral=True)
            return

        staked = sum(r["amount"] for r in rows)
        potential = 0
        lines = []
        for r in rows:
            label = dict(service.outcomes_for_market(r))[r["outcome"]]
            lock = "🔒" if r["status"] == "locked" else "🟢"
            odds = (r["total_pool"] or 0) / (r["outcome_pool"] or 1)
            returns = int(r["amount"] * odds)
            potential += returns
            lines.append(
                f"{lock} **{_market_label(r)}**\n"
                f"⤷ {r['amount']:,} 🪙 on **{label}** · `{odds:.2f}x` → **{returns:,}** 🪙 if it wins"
            )

        embed = discord.Embed(
            title="🎲 Your open bets",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=(
                f"{staked:,} 🪙 staked · {potential:,} 🪙 if everything wins · odds move until settlement · 🔒 = closed"
            )
        )
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
        resolved = await service.resolve_market(market["id"], winner)
        if not resolved:
            # Someone else claimed it first — a race, not an error. They already sent the
            # DMs and the staff recap; sending them again would pay nobody but would confuse.
            await interaction.followup.send("❌ Ce pari vient d'être réglé par quelqu'un d'autre.", ephemeral=True)
            return
        mark_dirty(self.bot)
        await refresh_market_message(self.bot, market["id"])
        await dm_bettors(self.bot, market["id"])
        await announce_result_to_staff(self.bot, market["id"], settled_by=interaction.user.mention)
        winner_label = dict(service.outcomes_for_market(market))[winner]
        await interaction.followup.send(
            f"✅ Réglé — **{winner_label}** a gagné. Les parieurs ont reçu un MP.", ephemeral=True
        )

    @bet.command(name="cancel", description="Cancel a community bet and refund every stake.")
    @app_commands.describe(bet="The bet to cancel")
    @app_commands.autocomplete(bet=_settleable_autocomplete)
    async def bet_cancel(self, interaction: discord.Interaction, bet: str) -> None:
        market = await self._settleable_or_error(interaction, bet)
        if market is None:
            return

        await interaction.response.defer(ephemeral=True)
        result = await service.void_market(market["id"])
        if not result.voided:
            # Someone else claimed it first — a race, not an error. They already sent the
            # DMs and the staff recap; sending them again would refund nobody but would confuse.
            await interaction.followup.send("❌ Ce pari vient d'être réglé par quelqu'un d'autre.", ephemeral=True)
            return
        mark_dirty(self.bot)
        await refresh_market_message(self.bot, market["id"])
        await dm_bettors(self.bot, market["id"])
        await announce_result_to_staff(self.bot, market["id"], settled_by=interaction.user.mention)
        note = (
            f" Your **{CREATE_FEE:,}** 🪙 opening fee came back too."
            if result.fee_refunded
            else f" The **{CREATE_FEE:,}** 🪙 opening fee is not refunded, since nobody else bet on it."
        )
        await interaction.followup.send(f"✅ Bet cancelled — all stakes refunded.{note}", ephemeral=True)

    async def _settleable_or_error(self, interaction: discord.Interaction, bet: str):
        """Resolve the autocomplete value to a market the caller is allowed to settle.

        The autocomplete already hides what they can't settle, but it is not a security
        boundary — the id can be typed by hand. This is the check that counts.
        """
        try:
            market_id = int(bet)
        except ValueError:
            await interaction.response.send_message("❌ Choisis un pari dans la liste.", ephemeral=True)
            return None

        market = await service.get_market(market_id)
        if not market or market["status"] not in ("open", "locked"):
            await interaction.response.send_message("❌ Ce pari n'est pas à régler.", ephemeral=True)
            return None

        bets = await service.get_bets(market_id)
        is_mod = interaction.user.guild_permissions.manage_messages
        if service.may_settle(market, bets, interaction.user.id, is_mod=is_mod):
            return market

        # Two very different refusals, and conflating them would be maddening: one says "not
        # your bet", the other says "you're a player in this one".
        if any(b["user_id"] == interaction.user.id for b in bets):
            await interaction.response.send_message(
                "❌ Tu as misé sur ce pari — tu ne peux pas l'arbitrer.\n"
                "C'est à un modérateur qui n'a rien misé dessus de trancher.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Seule la personne qui a ouvert ce pari (ou un modérateur) peut le régler.",
                ephemeral=True,
            )
        return None

    # -----------------------------------------------------------------
    # Fixture polling — create new markets from upcoming provider fixtures
    # -----------------------------------------------------------------

    async def poll_fixtures_now(self) -> dict[str, PollStats]:
        """Post a card for every new fixture from every provider. Returns per-provider stats.

        Each provider and each fixture is isolated: one failing provider (or one malformed
        fixture) must not stop the others from being posted.
        """
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        stats = {p.name: PollStats() for p in self.providers}

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return stats

        # Read the routing table once per poll, not once per fixture: a matchday is dozens of
        # cards and this is otherwise two queries each for an answer that cannot change mid-poll.
        routes = {cat: row["channel_id"] for cat, row in (await service.get_category_channels(guild_id)).items()}
        default_id = await service.get_betting_channel(guild_id)
        if not default_id and not routes:
            return stats  # /setup betting hasn't been run yet

        def channel_for(category: str):
            # An unrouted category falls back to the one betting channel, so a guild that never
            # ran the new /setup form behaves exactly as it did before routing existed.
            channel_id = routes.get(category, default_id)
            return guild.get_channel(channel_id) if channel_id else None

        # An open market whose card was deleted (or whose channel moved) is unbettable but
        # still blocks re-creation, since the fixture already exists. Re-post it.
        for market in await service.get_open_markets(guild_id):
            stat = stats.setdefault(market["provider"], PollStats())
            try:
                channel = channel_for(service.category_of_market(market))
                if channel and await self._ensure_card(channel, market):
                    stat.restored += 1
            except Exception:
                logger.exception(f"Failed to restore card for market {market['id']}")

        for provider in self.providers:
            stat = stats[provider.name]
            try:
                fixtures = await provider.list_upcoming(FIXTURE_LOOKAHEAD_DAYS)
            except Exception:
                logger.exception(f"Provider {provider.name} failed to list fixtures; skipping it this poll")
                stat.failed = True
                continue

            for fixture in fixtures:
                try:
                    channel = channel_for(service.category_for(fixture.sport, fixture.competition))
                    if channel is None:
                        continue
                    if await self._post_fixture(channel, provider.name, fixture):
                        stat.created += 1
                    else:
                        stat.existing += 1
                except Exception:
                    logger.exception(f"Failed to post fixture {provider.name}:{fixture.external_id}; skipping it")

        await self._refresh_boards(guild)
        return stats

    async def _refresh_boards(self, guild) -> None:
        """Redraw the pinned board in every routed channel.

        Only *routed* categories get one. The fallback channel is shared by everything that
        isn't routed, so a board there would claim to speak for a channel it doesn't own.
        """
        rows = await service.get_category_channels(guild.id)
        if not rows:
            return

        by_category: dict[str, list] = {}
        for market in sorted(await service.get_open_markets(guild.id), key=lambda m: m["start_time"]):
            by_category.setdefault(service.category_of_market(market), []).append(market)

        for category, row in rows.items():
            channel = guild.get_channel(row["channel_id"])
            if channel is None:
                continue
            # One unwritable channel must not cost the other boards their refresh.
            try:
                await self._refresh_board(channel, category, row["board_message_id"], by_category.get(category, []))
            except discord.HTTPException as e:
                logger.warning(f"Could not refresh the {category} betting board: {e}")

    async def _refresh_board(self, channel, category: str, board_message_id: int | None, markets) -> None:
        embed = build_board_embed(category, markets)
        if board_message_id:
            try:
                message = await channel.fetch_message(board_message_id)
                await message.edit(embed=embed)
                return
            except discord.NotFound:
                pass  # someone deleted it — fall through and post a fresh one

        message = await channel.send(embed=embed)
        await service.set_board_message(channel.guild.id, category, message.id)
        await pin(message)

    async def _ensure_card(self, channel, market) -> bool:
        """Re-post a market's card if its message is gone. Returns True if it was re-posted.

        Any existing bets are preserved — only the message is recreated, so the pool carries over.
        """
        if market["channel_id"] and market["message_id"]:
            old_channel = self.bot.get_channel(market["channel_id"])
            if old_channel is not None:
                try:
                    await old_channel.fetch_message(market["message_id"])
                    return False  # card is still there, nothing to do
                except discord.NotFound:
                    pass  # deleted — fall through and re-post
                except discord.HTTPException:
                    # Transient failure: assume it's still there rather than risk a duplicate.
                    return False

        bets = await service.get_bets(market["id"])
        view = MarketView(market["id"], service.outcomes_for_market(market))
        card = await channel.send(embed=build_market_embed(market, bets), view=view)
        await service.set_market_message(market["id"], channel.id, card.id)
        self.bot.add_view(view)
        logger.info(f"Re-posted missing card for market {market['id']}")
        return True

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

        # Seed before the card is drawn, so it opens on a real line rather than flashing
        # "—" until the next refresh.
        await service.seed_market(market_id)
        market = await service.get_market(market_id)
        bets = await service.get_bets(market_id)
        view = MarketView(market_id, service.outcomes_for_market(market))
        card = await channel.send(embed=build_market_embed(market, bets), view=view)
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
        locked = await service.get_locked_markets(guild_id)
        still_running: list[int] = []

        # One request per provider, not per market. Custom bets are settled by hand and have no
        # provider, so filtering by name skips them for free.
        for provider in self.providers:
            markets = [m for m in locked if m["provider"] == provider.name]
            if not markets:
                continue
            try:
                results = await provider.get_results([m["external_id"] for m in markets])
            except Exception:
                logger.exception(f"Provider {provider.name} failed to fetch results; will retry next tick")
                continue

            for market in markets:
                # One market failing to settle must not block every other market behind it.
                try:
                    if await self._settle_market(provider, market, results.get(market["external_id"])):
                        still_running.append(market["id"])
                except Exception:
                    logger.exception(f"Failed to settle market {market['id']}; will retry next tick")

        # A locked market that nothing settles freezes everyone's stakes in silence — a
        # community bet whose creator forgot, or a real match whose provider never reported a
        # usable result. Chase both. The claim is atomic, so this can't double-ping.
        #
        # Except a match still being played: it locks at kickoff and the reminder fires 2h later,
        # which for football lands mid-match. Excluding them from the *claim* (rather than
        # claiming and discarding) matters — the claim stamps resolve_reminded_at, which would
        # push the real reminder back 24h if the result never did arrive.
        for market in await service.claim_settle_reminders(guild_id, exclude_ids=still_running):
            try:
                await remind_to_settle(self.bot, market)
            except Exception:
                logger.exception(f"Failed to send settle reminder for market {market['id']}")

        # Last resort: nobody came. Give the coins back rather than hold them forever.
        for market in await service.get_stuck_markets(guild_id):
            try:
                await self._void_stuck(market)
            except Exception:
                logger.exception(f"Failed to auto-void stuck market {market['id']}")

    async def _void_stuck(self, market) -> None:
        logger.warning(
            f"Market {market['id']} ({_market_label(market)}) has been locked since "
            f"{market['start_time']} with no result — auto-refunding every stake"
        )
        result = await service.void_market(market["id"])
        if not result.voided:
            # Someone else settled it between the stuck-market scan and this call — the
            # other settler already sent the DMs and the recap.
            return
        mark_dirty(self.bot)
        await refresh_market_message(self.bot, market["id"])
        await dm_bettors(self.bot, market["id"])
        await announce_result_to_staff(
            self.bot,
            market["id"],
            settled_by=f"auto-void — aucun résultat après {service.STUCK_VOID_AFTER.days} jours",
        )

    async def _settle_market(self, provider: Provider, market, result: ResultDTO | None) -> bool:
        """Settle one market from an already-fetched result. See resolution_ticker for the batch.

        Returns True only while the provider says the match is *still being played*. The caller
        uses that to hold the settle reminder back: a result that isn't due yet is not a result
        that is late, and a football match is routinely still on the clock at the 2h mark the
        reminder fires on. Every other case returns False — including the two that look like
        "no result" but aren't the same thing at all (see below).
        """
        if result is None:
            # Unreachable API, a rate-limited request, or a status we don't know. We genuinely
            # don't know whether a result is late, and a provider that has stopped answering is
            # exactly what the reminder exists to surface. Don't hold it back.
            return False
        if result.status == "pending":
            return True

        if result.status == "finished":
            if result.winner is None:
                # Finished, but the winner is unreadable — the MSI case. This *is* the stuck
                # market the reminder exists for: only a mod can call it now. Don't hold it back.
                logger.warning(f"Market {market['id']} finished with no winner reported, will retry")
                return False
            settled = await service.resolve_market(market["id"], result.winner)
        else:
            settled = (await service.void_market(market["id"])).voided
        if not settled:
            # Lost the race to a mod's /bet resolve or /bet cancel — they already sent the
            # DMs and the recap.
            return False
        mark_dirty(self.bot)
        await refresh_market_message(self.bot, market["id"])
        await dm_bettors(self.bot, market["id"])
        await announce_result_to_staff(self.bot, market["id"], settled_by=f"ticker `{provider.name}` (auto)")
        return False  # settled — it is no longer locked, so there is nothing to remind about

    @resolution_ticker.before_loop
    async def before_resolution_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @resolution_ticker.error
    async def resolution_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"resolution_ticker error (will retry next tick): {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BettingCog(bot))

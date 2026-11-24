"""LoLdle: one champion a day, guessed privately, compared through a spoiler-free public grid."""

from __future__ import annotations

import datetime as dt
import io

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.currency.leaderboard import mark_dirty as mark_coins_dirty
from bot.cogs.loldle import catalog, embeds, hints, rules, service
from bot.cogs.loldle.events import Event, EventListener
from bot.cogs.loldle.views import GameView, PlayView
from bot.core.discord_utils import edit_if_changed

STALE_WIN = dt.timedelta(hours=24)  # older wins are claimed but not posted: nobody wants last week's news


class LoldleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._last_minute: int | None = None
        self._listener: EventListener | None = None
        # False until the first successful probe: a fresh boot must never launch into a dead Activity.
        self._activity_up = False

    async def cog_load(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info(f"LoLdle: {len(catalog.load())} champions loaded.")
        self.bot.add_view(PlayView())
        self.minute_ticker.start()
        if self._activity_url():
            self.activity_probe.start()
        # Retries on its own: an unreachable database must not break boot.
        self._listener = EventListener(
            self.bot.config.database_url,  # type: ignore[attr-defined]
            self._on_event,
            self._on_connect,
        )
        self._listener.start()

    async def cog_unload(self) -> None:
        self.minute_ticker.cancel()
        self.activity_probe.cancel()
        if self._listener:
            await self._listener.stop()
        if self._session:
            await self._session.close()

    @staticmethod
    def today() -> dt.date:
        return rules.paris_day(discord.utils.utcnow())

    # --- /loldle ----------------------------------------------------------

    @app_commands.command(name="loldle", description="Devine le champion LoL du jour.")
    @app_commands.describe(champion="Ton essai (vide : revoir ta partie et tes indices)")
    @app_commands.guild_only()
    async def loldle(self, interaction: discord.Interaction, champion: str | None = None) -> None:
        if champion is None:
            await self.launch_or_card(interaction)
        else:
            await self.play(interaction, champion)

    def _activity_url(self) -> str | None:
        return self.bot.config.loldle_activity_internal_url  # type: ignore[attr-defined]

    async def launch_or_card(self, interaction: discord.Interaction) -> None:
        """Open the Activity, or the private card when it is absent, down, or refused.

        launch_activity() must be the first response (no defer) and the probe is read from memory:
        nothing here may wait on the network inside Discord's 3 s window.
        """
        if not self._activity_url() or not self._activity_up:
            await self.play(interaction, None)
            return
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as exc:
            logger.warning(f"LoLdle: launch_activity refused (HTTP {exc.status}), showing the card")
            if interaction.response.is_done():
                await interaction.followup.send("Impossible d'ouvrir l'Activity pour l'instant.", ephemeral=True)
            else:
                await self.play(interaction, None)

    @loldle.autocomplete("champion")
    async def champion_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=c.name, value=c.id) for c in catalog.search(current)]

    async def play(self, interaction: discord.Interaction, champion: str | None) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id, user_id = interaction.guild_id, interaction.user.id
        await self._remember_player(interaction)
        day = self.today()
        answer = service.answer_of(await service.get_or_pick_day(guild_id, day, catalog.load()))
        note = None
        if champion is not None:
            guess = service.resolve_guess(champion, answer)
            if guess is None:
                await interaction.followup.send("Champion inconnu : choisis-le dans la liste.", ephemeral=True)
                return
            outcome = await service.record_guess(guild_id, day, user_id, guess, answer)
            if outcome.status == "duplicate":
                note = f"Tu as déjà essayé **{guess.name}**, cet essai ne compte pas."
            elif outcome.status == "solved_already":
                note = "Tu as déjà trouvé le champion du jour. Reviens demain !"
        await self._show(interaction, day, answer, note)

    async def _show(
        self, interaction: discord.Interaction, day: dt.date, answer: catalog.Champion, note: str | None
    ) -> None:
        guild_id, user_id = interaction.guild_id, interaction.user.id
        rows = await service.guesses(guild_id, day, user_id)
        won = await service.result(guild_id, day, user_id)
        owned = await service.hints(guild_id, day, user_id)
        await interaction.followup.send(
            content=note or discord.utils.MISSING,
            embed=embeds.build_game_embed(day, rows, won, answer),
            view=GameView(day, owned, len(rows), won is not None),
            ephemeral=True,
        )

    async def _remember_player(self, interaction: discord.Interaction) -> None:
        """Best effort: the Activity shows names from here, but it must never cost the member their game."""
        try:
            user = interaction.user
            avatar = user.avatar.key if user.avatar else None
            await service.upsert_player(interaction.guild_id, user.id, user.display_name, avatar)
        except Exception:
            logger.exception("LoLdle: could not record the player")

    # --- announcements (the queue is the only path) -----------------------

    async def _drain_announcements(self, guild_id: int) -> None:
        """Post every win not yet announced. Never raises: it runs from a ticker and from the listener."""
        if not self.bot.is_ready():
            # The listener starts in setup_hook: before READY the channel cache is empty, so a claim
            # would swallow every pending win. The minute ticker waits for ready and catches up.
            return
        try:
            rows = sorted(await service.claim_announcements(guild_id), key=lambda r: r["solved_at"])
            if not rows:
                return
            config = await service.get_config(guild_id)
            channel = self.bot.get_channel(config["channel_id"]) if config else None
            if channel is None:  # claimed anyway: a channel set up later must not replay old wins
                return
            now = discord.utils.utcnow()
            days = await service.all_days(guild_id)
        except Exception:
            logger.exception("LoLdle: could not claim announcements")
            return
        for row in rows:
            if now - row["solved_at"] > STALE_WIN:
                continue
            try:
                solved = await service.solved_days(guild_id, row["user_id"])
                count = rules.streak(days, solved, row["day"])
                await channel.send(
                    embed=embeds.build_public_embed(f"<@{row['user_id']}>", row, count),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # the win is committed: one failure must not cost the others their post
                logger.exception("LoLdle: could not post a result")

    async def _on_event(self, event: Event) -> None:
        if event.guild_id != self.bot.config.guild_id:  # type: ignore[attr-defined]
            return
        if event.kind == "win":
            await self._drain_announcements(event.guild_id)
        elif event.kind == "hint":
            mark_coins_dirty(self.bot)

    async def _on_connect(self) -> None:
        """Notifications sent while disconnected are lost: resync from the database."""
        await self._drain_announcements(self.bot.config.guild_id)  # type: ignore[attr-defined]
        mark_coins_dirty(self.bot)

    # --- hints ------------------------------------------------------------

    async def buy(self, interaction: discord.Interaction, kind: str, day: dt.date) -> None:
        await interaction.response.defer(ephemeral=True)
        if day != self.today():
            await interaction.followup.send("C'est un nouveau jour : relance `/loldle`.", ephemeral=True)
            return
        guild_id, user_id = interaction.guild_id, interaction.user.id
        row = await service.day_row(guild_id, day)
        if row is None:
            await interaction.followup.send("Relance `/loldle`.", ephemeral=True)
            return
        # Rendered first: if the CDN is down the member must not have paid for nothing.
        try:
            content = await hints.render_hint(kind, service.answer_of(row), guild_id, day, self._session)
        except hints.HintUnavailable as exc:
            logger.warning(f"LoLdle: hint {kind} unavailable: {exc}")
            await interaction.followup.send("Indice indisponible pour l'instant, rien n'a été débité.", ephemeral=True)
            return
        if day != self.today():  # the download can straddle midnight: never sell a finished game
            await interaction.followup.send("C'est un nouveau jour : relance `/loldle`.", ephemeral=True)
            return
        out = await service.buy_hint(guild_id, day, user_id, kind)
        if out.status == "poor":
            text = f"Il te faut **{rules.HINTS[kind]}** 🪙, tu en as **{out.balance}**. Rien n'a été débité."
            await interaction.followup.send(text, ephemeral=True)
            return
        if out.status == "locked":
            await interaction.followup.send("Cet indice n'est pas disponible pour l'instant.", ephemeral=True)
            return
        text = content.text
        if out.status == "bought":
            mark_coins_dirty(self.bot)
            text += f"\n-{rules.HINTS[kind]} 🪙 · solde : **{out.balance}** · +1 au score"
        extra = {}
        if content.file_bytes is not None:
            extra["file"] = discord.File(io.BytesIO(content.file_bytes), filename=hints.FILENAME)
        await interaction.followup.send(text, ephemeral=True, **extra)

    # --- board and uptime -------------------------------------------------

    async def render_board(self, guild_id: int) -> discord.Embed:
        today = self.today()
        previous = await service.day_row(guild_id, today - dt.timedelta(days=1))
        yesterday = service.answer_of(previous).name if previous else None
        days = await service.all_days(guild_id)
        solved = await service.solved_by_user(guild_id)
        streaks = {user: rules.streak(days, won, today) for user, won in solved.items()}
        return embeds.build_board_embed(
            yesterday,
            await service.solved_count(guild_id, today),
            await service.board_rows(guild_id, today),
            streaks,
        )

    async def redraw_board(self, guild_id: int) -> None:
        config = await service.get_config(guild_id)
        if not config or not config["board_message_id"]:
            return
        channel = self.bot.get_channel(config["channel_id"])
        if channel is None:
            return
        message = channel.get_partial_message(config["board_message_id"])
        await edit_if_changed(message, label="LoLdle", embed=await self.render_board(guild_id), view=PlayView())

    # --- midnight recap and 09:00 ping -------------------------------------

    async def _daily_posts(self, guild_id: int, now: dt.datetime, day: dt.date) -> None:
        config = await service.get_config(guild_id)
        channel = self.bot.get_channel(config["channel_id"]) if config else None
        if channel is None:
            return
        await self._post_recap(channel, guild_id, day)
        if rules.in_ping_window(now):
            await self._post_ping(channel, guild_id, day)

    async def _post_recap(self, channel, guild_id: int, day: dt.date) -> None:
        if await service.claim_recap(guild_id, day) != "send":
            return
        try:
            yesterday = day - dt.timedelta(days=1)
            row = await service.day_row(guild_id, yesterday)
            results = await service.day_results(guild_id, yesterday)
            dnf = await service.day_dnf(guild_id, yesterday)
            days = await service.all_days(guild_id)
            solved = await service.solved_by_user(guild_id)
            streaks = {r["user_id"]: rules.streak(days, solved.get(r["user_id"], set()), yesterday) for r in results}
            name = service.answer_of(row).name if row else None
            await channel.send(
                embed=embeds.build_recap_embed(name, results, dnf, streaks),
                allowed_mentions=discord.AllowedMentions.none(),
                view=PlayView(),
            )
        except discord.Forbidden:  # retrying every minute would change nothing
            logger.warning(f"LoLdle: no permission to post the recap in {channel.id}")
        except Exception:
            logger.exception("LoLdle: recap failed, retrying next minute")
            await service.release_recap(guild_id, day)

    async def _post_ping(self, channel, guild_id: int, day: dt.date) -> None:
        if not await service.claim_ping(guild_id, day):
            return
        try:
            targets = await service.ping_targets(guild_id, day)
            if not targets:
                return
            if len(targets) > rules.MAX_PING_USERS:
                logger.warning(f"LoLdle: {len(targets)} players to ping, only the first {rules.MAX_PING_USERS} are")
                targets = targets[: rules.MAX_PING_USERS]
            mentions = " ".join(f"<@{u}>" for u in targets)
            message = await channel.send(
                f"{mentions}\n🎯 Le LoLdle du jour t'attend !",
                # Exactly these members: nothing typed or stored elsewhere can widen the ping.
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[discord.Object(id=u) for u in targets]
                ),
                view=PlayView(),
            )
        except discord.Forbidden:
            logger.warning(f"LoLdle: no permission to ping in {channel.id}")
            return
        except Exception:
            logger.exception("LoLdle: ping failed, retrying next minute")
            await service.release_ping(guild_id, day)
            return
        await service.set_ping_message(guild_id, day, message.id)
        previous = await service.previous_ping(guild_id, day)
        if previous is None:
            return
        old_day, old_id = previous
        try:
            await channel.get_partial_message(old_id).delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            logger.warning(f"LoLdle: could not delete the previous ping: {exc.status}")
        await service.set_ping_message(guild_id, old_day, None)

    @tasks.loop(minutes=1)
    async def minute_ticker(self) -> None:
        """Counts uptime and redraws the board. Each step guards itself: this body never raises."""
        now = discord.utils.utcnow()
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        day = rules.paris_day(now)
        minute = int(now.timestamp() // 60)
        # discord.ext.tasks re-fires with no delay after an overrun: one wall-clock minute counts once.
        if minute != self._last_minute:
            self._last_minute = minute
            try:
                await service.tick_day(guild_id, day, catalog.load())
            except Exception:
                logger.exception("LoLdle: uptime tick failed")
        try:
            await self._drain_announcements(guild_id)
        except Exception:
            logger.exception("LoLdle: announcement drain failed")
        try:
            await self._daily_posts(guild_id, now, day)
        except Exception:
            logger.exception("LoLdle: daily posts failed")
        # Every tick, not off a flag: edit_if_changed skips identical renders and swallows HTTP
        # errors, so a one-shot flag would lose the midnight redraw on a single Discord 503.
        try:
            await self.redraw_board(guild_id)
        except Exception:
            logger.exception("LoLdle: could not redraw the board")

    @minute_ticker.before_loop
    async def _before_minute(self) -> None:
        await self.bot.wait_until_ready()

    @minute_ticker.error
    async def _minute_error(self, exc: BaseException) -> None:
        logger.opt(exception=exc).error("LoLdle ticker crashed — restarting.")
        self.minute_ticker.restart()

    # --- Activity health ---------------------------------------------------

    @tasks.loop(seconds=30)
    async def activity_probe(self) -> None:
        """Keeps `_activity_up` fresh so /loldle can decide without a network call. Never raises."""
        up = False
        reason = "unreachable"
        try:
            async with self._session.get(  # type: ignore[union-attr]
                f"{self._activity_url()}/health", timeout=aiohttp.ClientTimeout(total=2)
            ) as resp:
                up = resp.status == 200
                reason = f"HTTP {resp.status}"
        except Exception as exc:
            reason = type(exc).__name__
        # Only the transitions are worth a line: a probe every 30 s would otherwise fill the log.
        if up and not self._activity_up:
            logger.info("LoLdle: the Activity is reachable")
        elif not up and self._activity_up:
            logger.warning(f"LoLdle: the Activity went down ({reason}), /loldle falls back to the private card")
        self._activity_up = up

    @activity_probe.error
    async def _probe_error(self, exc: BaseException) -> None:
        logger.opt(exception=exc).error("LoLdle probe crashed — restarting.")
        self.activity_probe.restart()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LoldleCog(bot))

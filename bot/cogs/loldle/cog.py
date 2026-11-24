"""LoLdle: one champion a day, guessed privately, compared through a spoiler-free public grid."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.currency.leaderboard import mark_dirty as mark_coins_dirty
from bot.cogs.loldle import catalog, embeds, hints, images, rules, service
from bot.cogs.loldle.events import Event, EventListener
from bot.cogs.loldle.views import GameView, PlayView
from bot.core.discord_utils import edit_if_changed

STALE_WIN = dt.timedelta(hours=24)  # older wins are claimed but not posted: nobody wants last week's news
POST_BACKOFF = 60.0  # after a 429 on a game post, leave that message alone this long
AVATAR_TIMEOUT = 5.0
POST_RETRY = 30.0  # a draw that failed (DB timeout, Discord 5xx) is tried again this much later
POST_MAX_FAILURES = 5  # ...at most this many times, so a deterministic failure cannot loop all day


class LoldleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._last_minute: int | None = None
        self._listener: EventListener | None = None
        # False until the first successful probe: a fresh boot must never launch into a dead Activity.
        self._activity_up = False
        # (day, user_id) whose public game post must be redrawn: drained swap-then-iterate by post_ticker,
        # so a burst of guesses costs one edit per member per tick.
        self._dirty_posts: set[tuple[dt.date, int]] = set()
        self._post_cooldown: dict[tuple[dt.date, int], float] = {}
        self._avatars: dict[tuple[dt.date, int], bytes | None] = {}
        self._post_failures: dict[tuple[dt.date, int], int] = {}

    async def cog_load(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info(f"LoLdle: {len(catalog.load())} champions loaded.")
        self.bot.add_view(PlayView())
        self.minute_ticker.start()
        self.post_ticker.start()
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
        self.post_ticker.cancel()
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
        except Exception:
            logger.exception("LoLdle: could not claim announcements")
            return
        for row in rows:
            if now - row["solved_at"] > STALE_WIN:
                continue
            try:
                await service.finish_post(guild_id, row["day"], row["user_id"])
            except Exception:  # the win is committed: one failure must not cost the others their post
                logger.exception("LoLdle: could not finish a game post")
            self.mark_post(row["user_id"], row["day"])

    async def _on_event(self, event: Event) -> None:
        if event.guild_id != self.bot.config.guild_id:  # type: ignore[attr-defined]
            return
        # Yesterday too: a guess at 23:59:58 is delivered after midnight and must still be drawn.
        if event.kind in ("guess", "hint") and event.day >= self.today() - dt.timedelta(days=1):
            self.mark_post(event.user_id, event.day)
        if event.kind == "win":
            await self._drain_announcements(event.guild_id)
        elif event.kind == "hint":
            mark_coins_dirty(self.bot)

    async def _on_connect(self) -> None:
        """Notifications sent while disconnected are lost: resync from the database."""
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        await self._drain_announcements(guild_id)
        mark_coins_dirty(self.bot)
        try:
            today = self.today()
            for user_id in await service.day_grids(guild_id, today):
                self.mark_post(user_id, today)
        except Exception:
            logger.exception("LoLdle: could not resync the game posts")

    # --- public game posts ("X joue au LoLdle") ------------------------------

    def mark_post(self, user_id: int, day: dt.date | None = None) -> None:
        self._dirty_posts.add((day or self.today(), user_id))

    async def _player_face(self, guild_id: int, day: dt.date, user_id: int) -> tuple[str, bytes | None]:
        """Display name and avatar bytes; the avatar is fetched once a day and may be None."""
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if member is None:
            rows = await service.player_names(guild_id, [user_id])
            return (rows[0]["display_name"] if rows else "Joueur"), None
        key = (day, user_id)
        if key not in self._avatars:
            try:
                asset = member.display_avatar.with_size(64)
                self._avatars[key] = await asyncio.wait_for(asset.read(), AVATAR_TIMEOUT)
            except Exception as err:  # initials are drawn instead
                logger.debug(f"LoLdle: no avatar for a game post ({type(err).__name__})")
                self._avatars[key] = None
        return member.display_name, self._avatars[key]

    async def _draw_post(self, guild_id: int, day: dt.date, user_id: int) -> None:
        """Create or edit one member's game post. Raises nothing the ticker cannot log."""
        key = (day, user_id)
        until = self._post_cooldown.get(key)
        if until is not None and time.monotonic() < until:
            self._dirty_posts.add(key)  # rate limited: try again once the back-off is over
            return
        config = await service.get_config(guild_id)
        channel = self.bot.get_channel(config["channel_id"]) if config else None
        if channel is None:
            return
        rows = await service.guesses(guild_id, day, user_id)
        if not rows:
            return
        post = await service.get_post(guild_id, day, user_id)
        if post is None:
            await service.claim_post(guild_id, day, user_id, channel.id)
            post = {"channel_id": channel.id, "message_id": None, "finished": False}
        message_id = post["message_id"]
        if post["channel_id"] != channel.id:  # /setup moved the channel: start a fresh post there
            await service.set_post_channel(guild_id, day, user_id, channel.id)
            message_id = None
        won = await service.result(guild_id, day, user_id)
        owned = await service.hints(guild_id, day, user_id)
        solved = won is not None
        # A past day is over whatever the row says: the recap may have run before this post existed.
        finished = solved or bool(post["finished"]) or day < self.today()
        streak = 0
        if solved:
            streak = rules.streak(await service.all_days(guild_id), await service.solved_days(guild_id, user_id), day)
        name, avatar = await self._player_face(guild_id, day, user_id)
        content = embeds.build_game_post(f"<@{user_id}>", len(rows), len(owned), solved, finished, streak)
        card = images.PlayerCard(name, avatar, [service.cells_of(r) for r in rows], len(rows), solved, finished)
        png = await asyncio.to_thread(images.render_game, card)
        file = discord.File(io.BytesIO(png), filename="partie.png")
        if message_id is not None:
            try:
                await channel.get_partial_message(message_id).edit(
                    content=content,
                    attachments=[file],
                    view=PlayView(),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            except discord.NotFound:  # deleted by hand: post a new one below
                await service.set_post_message(guild_id, day, user_id, None)
                file = discord.File(io.BytesIO(png), filename="partie.png")
            except discord.HTTPException as exc:
                if exc.status == 429:
                    self._post_cooldown[key] = time.monotonic() + POST_BACKOFF
                    self._dirty_posts.add(key)
                    logger.warning(f"LoLdle: rate limited on a game post, backing off {POST_BACKOFF:.0f}s")
                    return
                raise
        message = await channel.send(
            content=content,
            file=file,
            view=PlayView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await service.set_post_message(guild_id, day, user_id, message.id)

    @tasks.loop(seconds=5)
    async def post_ticker(self) -> None:
        """Redraw every marked game post once. Each post guards itself: this body never raises."""
        if not self._dirty_posts:
            return
        pending, self._dirty_posts = self._dirty_posts, set()
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        for day, user_id in sorted(pending):
            key = (day, user_id)
            try:
                await self._draw_post(guild_id, day, user_id)
                self._post_failures.pop(key, None)
            except Exception:
                # The win or the midnight "n'a pas trouvé" may be this very draw: try again, a bounded
                # number of times, rather than leaving the post stuck in its previous state.
                failures = self._post_failures.get(key, 0) + 1
                self._post_failures[key] = failures
                if failures < POST_MAX_FAILURES:
                    logger.exception(f"LoLdle: could not draw a game post (attempt {failures}), retrying")
                    self._post_cooldown[key] = time.monotonic() + POST_RETRY
                    self._dirty_posts.add(key)
                else:
                    logger.exception("LoLdle: giving up on a game post after repeated failures")
        oldest = self.today() - dt.timedelta(days=1)
        for cache in (self._avatars, self._post_cooldown, self._post_failures):
            for k in [k for k in cache if k[0] < oldest]:
                cache.pop(k, None)

    @post_ticker.before_loop
    async def _before_posts(self) -> None:
        await self.bot.wait_until_ready()

    @post_ticker.error
    async def _posts_error(self, exc: BaseException) -> None:
        logger.opt(exception=exc).error("LoLdle post ticker crashed — restarting.")
        self.post_ticker.restart()

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
            # Games left open yesterday become "n'a pas trouvé": the recap claim makes this once.
            for post in await service.unfinished_posts(guild_id, yesterday):
                await service.finish_post(guild_id, yesterday, post["user_id"])
                self.mark_post(post["user_id"], yesterday)
            row = await service.day_row(guild_id, yesterday)
            results = await service.day_results(guild_id, yesterday)
            dnf = await service.day_dnf(guild_id, yesterday)
            streak = rules.group_streak(
                await service.all_days(guild_id), await service.group_solved_days(guild_id), yesterday
            )
            name = service.answer_of(row).name if row else None
            content = embeds.build_recap_text(name, streak, results, dnf)
            extra = {}
            try:
                cards = await self._recap_cards(guild_id, yesterday, results, dnf)
                if cards:
                    png = await asyncio.to_thread(images.render_recap, yesterday, streak, cards)
                    extra["file"] = discord.File(io.BytesIO(png), filename="recap.png")
            except Exception as err:  # the text still goes out: a broken picture must not block the recap
                logger.warning(f"LoLdle: recap picture failed ({type(err).__name__}), sending text only")
            await channel.send(
                content=content,
                allowed_mentions=discord.AllowedMentions.none(),
                view=PlayView(),
                **extra,
            )
        except discord.Forbidden:  # retrying every minute would change nothing
            logger.warning(f"LoLdle: no permission to post the recap in {channel.id}")
        except Exception:
            logger.exception("LoLdle: recap failed, retrying next minute")
            await service.release_recap(guild_id, day)

    async def _recap_cards(self, guild_id: int, day: dt.date, results, dnf) -> list[images.PlayerCard]:
        """Winners by tries (ties crowned together), then the members who did not find it."""
        if not results and not dnf:
            return []
        grids = await service.day_grids(guild_id, day)
        ordered = sorted(results, key=lambda r: (r["tries"], r.get("score", 0), r["user_id"]))
        best = ordered[0]["tries"] if ordered else None
        cards = []
        for r in ordered:
            name, avatar = await self._player_face(guild_id, day, r["user_id"])
            rows = grids.get(r["user_id"], [])
            cards.append(images.PlayerCard(name, avatar, rows, r["tries"], True, True, r["tries"] == best))
        for user_id in dnf:
            name, avatar = await self._player_face(guild_id, day, user_id)
            rows = grids.get(user_id, [])
            cards.append(images.PlayerCard(name, avatar, rows, len(rows), False, True))
        return cards

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

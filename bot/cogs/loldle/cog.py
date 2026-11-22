"""LoLdle: one champion a day, guessed privately, compared through a spoiler-free public grid."""

from __future__ import annotations

import datetime as dt
import io

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.announce.service import allowed_mentions_for, ping_content
from bot.cogs.currency.leaderboard import mark_dirty as mark_coins_dirty
from bot.cogs.loldle import catalog, embeds, hints, rules, service
from bot.cogs.loldle.views import GameView
from bot.core.discord_utils import edit_if_changed


class LoldleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._last_minute: int | None = None

    async def cog_load(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info(f"LoLdle: {len(catalog.load())} champions loaded.")
        self.minute_ticker.start()

    async def cog_unload(self) -> None:
        self.minute_ticker.cancel()
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
        await self.play(interaction, champion)

    @loldle.autocomplete("champion")
    async def champion_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=c.name, value=c.id) for c in catalog.search(current)]

    async def play(self, interaction: discord.Interaction, champion: str | None) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id, user_id = interaction.guild_id, interaction.user.id
        day = self.today()
        answer = service.answer_of(await service.get_or_pick_day(guild_id, day, catalog.load()))
        note = None
        if champion is not None:
            # The stored answer first: a refreshed catalog may no longer contain it.
            is_answer = catalog.normalise(champion) in (catalog.normalise(answer.id), catalog.normalise(answer.name))
            guess = answer if is_answer else catalog.find(champion)
            if guess is None:
                await interaction.followup.send("Champion inconnu : choisis-le dans la liste.", ephemeral=True)
                return
            outcome = await service.record_guess(guild_id, day, user_id, guess, answer)
            if outcome.status == "duplicate":
                note = f"Tu as déjà essayé **{guess.name}**, cet essai ne compte pas."
            elif outcome.status == "solved_already":
                note = "Tu as déjà trouvé le champion du jour. Reviens demain !"
            elif outcome.result is not None:  # only the request that claimed the win announces it
                await self._announce(interaction, outcome.result, day)
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

    async def _announce(self, interaction: discord.Interaction, won, day: dt.date) -> None:
        """Best effort: a channel problem must never cost the member their own reply."""
        guild_id, user_id = interaction.guild_id, interaction.user.id
        try:
            config = await service.get_config(guild_id)
            channel = self.bot.get_channel(config["channel_id"]) if config else None
            if channel is None:
                return
            days = await service.all_days(guild_id)
            count = rules.streak(days, await service.solved_days(guild_id, user_id), day)
            await channel.send(
                embed=embeds.build_public_embed(interaction.user.mention, won, count),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:  # the win is already committed: the member must still get their card
            logger.exception("LoLdle: could not post a result")

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
        await edit_if_changed(message, label="LoLdle", embed=await self.render_board(guild_id))

    # --- midnight recap and 09:00 ping -------------------------------------

    async def _daily_posts(self, guild_id: int, now: dt.datetime, day: dt.date) -> None:
        config = await service.get_config(guild_id)
        channel = self.bot.get_channel(config["channel_id"]) if config else None
        if channel is None:
            return
        await self._post_recap(channel, guild_id, day)
        if rules.in_ping_window(now):
            await self._post_ping(channel, guild_id, day, config["role_id"])

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
            )
        except discord.Forbidden:  # retrying every minute would change nothing
            logger.warning(f"LoLdle: no permission to post the recap in {channel.id}")
        except Exception:
            logger.exception("LoLdle: recap failed, retrying next minute")
            await service.release_recap(guild_id, day)

    async def _post_ping(self, channel, guild_id: int, day: dt.date, role_id: int | None) -> None:
        # Claimed even with no role, so a role added later today doesn't ping in the afternoon.
        if not await service.claim_ping(guild_id, day):
            return
        guild = self.bot.get_guild(guild_id)
        role = guild.get_role(role_id) if guild and role_id else None
        if role is None:
            return
        try:
            message = await channel.send(
                f"{ping_content(role)} 🎯 Le LoLdle du jour t'attend : `/loldle`",
                allowed_mentions=allowed_mentions_for(role),
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LoldleCog(bot))

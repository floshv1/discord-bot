from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.betting import service as betting_service
from bot.cogs.betting.embeds import build_pnl_embed
from bot.cogs.currency import service
from bot.cogs.currency.embeds import (
    CURRENCY_EMOJI,
    CURRENCY_NAME,
    build_history_embed,
    build_leaderboard_embed,
    build_transactions_log_embed,
)
from bot.cogs.currency.views import CurrencyPanelView, _fmt_duration
from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")


class CurrencyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._leaderboard_dirty = False

    async def cog_load(self) -> None:
        self.bot.add_view(CurrencyPanelView())
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        # The betting cog stakes the house on every market it opens, so its wallet has to
        # exist before any market does.
        await service.ensure_house_wallet(guild_id)
        # Start the mirror at the end of the ledger, or the first boot after this ships would
        # dump every transaction ever made into the log channel.
        await service.start_log_cursor_at_latest(guild_id)
        self.leaderboard_ticker.start()
        self.transaction_log_ticker.start()

    async def cog_unload(self) -> None:
        self.leaderboard_ticker.cancel()
        self.transaction_log_ticker.cancel()

    @tasks.loop(seconds=20)
    async def transaction_log_ticker(self) -> None:
        """Mirror new ledger rows into the audit-log channel.

        Reads the ledger rather than being called from the ten-odd places that move coins:
        a transaction cannot be missed by someone forgetting to add a call.
        """
        config = self.bot.config  # type: ignore[attr-defined]
        channel = self.bot.get_channel(config.log_channel_id)
        if channel is None:
            return
        rows = await service.drain_new_transactions(config.guild_id)
        if not rows:
            return
        await channel.send(
            embed=build_transactions_log_embed(rows),
            allowed_mentions=discord.AllowedMentions.none(),  # the log must not ping anyone
        )

    @transaction_log_ticker.before_loop
    async def before_transaction_log_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @transaction_log_ticker.error
    async def transaction_log_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"transaction_log_ticker error (will retry next tick): {error}")

    def mark_leaderboard_dirty(self) -> None:
        """Ask for a leaderboard redraw on the next tick (see currency/leaderboard.py)."""
        self._leaderboard_dirty = True

    currency = app_commands.Group(name="currency", description=f"{CURRENCY_NAME} wallet commands.")

    @app_commands.command(name="balance", description=f"Check your (or someone else's) {CURRENCY_NAME} balance.")
    @app_commands.describe(user="Whose balance to check (defaults to you)")
    async def balance(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        wallet, created = await service.get_or_create_wallet(interaction.guild_id, target.id)
        if created:
            self.mark_leaderboard_dirty()  # a new wallet belongs on the leaderboard
        await interaction.response.send_message(
            f"{CURRENCY_EMOJI} **{target.display_name}** has **{wallet['balance']:,}** {CURRENCY_NAME}.",
            ephemeral=True,
        )

    @app_commands.command(name="claim", description=f"Claim your free daily {CURRENCY_NAME}.")
    async def claim(self, interaction: discord.Interaction) -> None:
        new_balance = await service.claim(interaction.guild_id, interaction.user.id)
        if new_balance is None:
            remaining = await service.claim_cooldown_remaining(interaction.guild_id, interaction.user.id)
            wait = _fmt_duration(remaining or 0)
            await interaction.response.send_message(
                f"❌ You've already claimed today. Resets at midnight — **{wait}** to go.", ephemeral=True
            )
            return
        self.mark_leaderboard_dirty()
        await interaction.response.send_message(
            f"{CURRENCY_EMOJI} You claimed **{service.CLAIM_AMOUNT}** {CURRENCY_NAME}! "
            f"New balance: **{new_balance:,}**.",
            ephemeral=True,
        )

    @currency.command(name="history", description=f"Voir l'historique des {CURRENCY_NAME} d'un membre.")
    @app_commands.describe(user="Membre dont on veut l'historique")
    @app_commands.default_permissions(manage_guild=True)
    async def currency_history(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        wallet, _ = await service.get_or_create_wallet(interaction.guild_id, user.id)
        rows = await service.get_history(interaction.guild_id, user.id)

        embed = build_history_embed(user.display_name, rows, wallet["balance"])

        # The ledger should add up to the wallet. If it doesn't, say so rather than quietly
        # showing a total that disagrees with itself — that mismatch IS the incident.
        total = await service.ledger_total(interaction.guild_id, user.id)
        if total != wallet["balance"]:
            embed.add_field(
                name="⚠️ Incohérence",
                value=(
                    f"Le solde (**{wallet['balance']:,}**) ne correspond pas à la somme du "
                    f"journal (**{total:,}**) — écart de **{wallet['balance'] - total:+,}**."
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @currency.command(name="give", description=f"Grant or remove {CURRENCY_NAME} for a member.")
    @app_commands.describe(user="Member to adjust", amount="Amount to add (negative to remove)")
    @app_commands.default_permissions(manage_guild=True)
    async def currency_give(self, interaction: discord.Interaction, user: discord.Member, amount: int) -> None:
        if amount == 0:
            await interaction.response.send_message("❌ Amount must be non-zero.", ephemeral=True)
            return
        new_balance = await service.grant(interaction.guild_id, user.id, amount, "admin_grant")
        if new_balance is None:
            await interaction.response.send_message(
                f"❌ That would take {user.mention} below zero. Check their balance first.", ephemeral=True
            )
            return
        self.mark_leaderboard_dirty()
        await interaction.response.send_message(
            f"✅ Adjusted {user.mention} by **{amount:+,}**. New balance: **{new_balance:,}**.",
            ephemeral=True,
        )

    @tasks.loop(seconds=30)
    async def leaderboard_ticker(self) -> None:
        # Only redraw when a balance actually moved, so the ticker is nearly free when idle.
        if not self._leaderboard_dirty:
            return
        self._leaderboard_dirty = False
        await self._update_leaderboard_message()
        await self._update_pnl_message()

    @leaderboard_ticker.before_loop
    async def before_leaderboard_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @leaderboard_ticker.error
    async def leaderboard_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"leaderboard_ticker error (will retry next tick): {error}")

    async def _update_leaderboard_message(self) -> None:
        pool = get_pool()
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]

        row = await pool.fetchrow(
            "SELECT channel_id, message_id FROM currency_leaderboard WHERE guild_id = $1",
            guild_id,
        )
        if not row or not row["message_id"]:
            return

        rows = await service.top_balances(guild_id)
        names = {}
        for r in rows:
            user = self.bot.get_user(r["user_id"])
            if user:
                names[r["user_id"]] = user.display_name

        updated = datetime.datetime.now(PARIS_TZ).strftime("%d/%m at %H:%M")
        embed = build_leaderboard_embed(rows, names, updated)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(row["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(row["message_id"])
            await msg.edit(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"Failed to update currency leaderboard: {e}")

    async def _update_pnl_message(self) -> None:
        """The profit-and-loss board — who is actually *good* at betting, not just rich."""
        pool = get_pool()
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]

        row = await pool.fetchrow(
            "SELECT channel_id, pnl_message_id FROM currency_leaderboard WHERE guild_id = $1",
            guild_id,
        )
        if not row or not row["pnl_message_id"]:
            return  # /setup currency predates this board — re-run it to get one

        rows = await betting_service.betting_pnl(guild_id)
        names = {}
        for r in rows:
            user = self.bot.get_user(r["user_id"])
            if user:
                names[r["user_id"]] = user.display_name

        updated = datetime.datetime.now(PARIS_TZ).strftime("%d/%m à %H:%M")
        embed = build_pnl_embed(rows, names, updated)

        channel = self.bot.get_channel(row["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(row["pnl_message_id"])
            await msg.edit(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"Failed to update P&L board: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CurrencyCog(bot))

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.currency import service
from bot.cogs.currency.embeds import CURRENCY_EMOJI, CURRENCY_NAME, build_leaderboard_embed
from bot.cogs.currency.views import CurrencyPanelView, _fmt_duration
from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")


class CurrencyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._leaderboard_dirty = False

    async def cog_load(self) -> None:
        self.bot.add_view(CurrencyPanelView())
        self.leaderboard_ticker.start()

    async def cog_unload(self) -> None:
        self.leaderboard_ticker.cancel()

    def mark_leaderboard_dirty(self) -> None:
        """Ask for a leaderboard redraw on the next tick (see currency/leaderboard.py)."""
        self._leaderboard_dirty = True

    currency = app_commands.Group(name="currency", description=f"{CURRENCY_NAME} wallet commands.")

    @app_commands.command(name="balance", description=f"Check your (or someone else's) {CURRENCY_NAME} balance.")
    @app_commands.describe(user="Whose balance to check (defaults to you)")
    async def balance(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        wallet = await service.get_or_create_wallet(interaction.guild_id, target.id)
        self.mark_leaderboard_dirty()  # may have just lazily created a wallet
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CurrencyCog(bot))

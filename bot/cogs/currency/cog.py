from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.currency import service
from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")
CURRENCY_NAME = "FloshCoins"
CURRENCY_EMOJI = "🪙"


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


class CurrencyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.leaderboard_ticker.start()

    async def cog_unload(self) -> None:
        self.leaderboard_ticker.cancel()

    currency = app_commands.Group(name="currency", description=f"{CURRENCY_NAME} wallet commands.")

    @app_commands.command(name="balance", description=f"Check your (or someone else's) {CURRENCY_NAME} balance.")
    @app_commands.describe(user="Whose balance to check (defaults to you)")
    async def balance(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        wallet = await service.get_or_create_wallet(interaction.guild_id, target.id)
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
                f"❌ You've already claimed today. Try again in **{wait}**.", ephemeral=True
            )
            return
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
        await interaction.response.send_message(
            f"✅ Adjusted {user.mention} by **{amount:+,}**. New balance: **{new_balance:,}**.",
            ephemeral=True,
        )

    @tasks.loop(minutes=15)
    async def leaderboard_ticker(self) -> None:
        await self._update_leaderboard_message()

    @leaderboard_ticker.before_loop
    async def before_leaderboard_ticker(self) -> None:
        await self.bot.wait_until_ready()

    @leaderboard_ticker.error
    async def leaderboard_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"leaderboard_ticker error (will retry next tick): {error}")

    async def _update_leaderboard_message(self) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        if not config.currency_leaderboard_channel_id:
            return

        pool = get_pool()
        guild_id = config.guild_id

        row = await pool.fetchrow(
            "SELECT channel_id, message_id FROM currency_leaderboard WHERE guild_id = $1",
            guild_id,
        )
        if not row or not row["message_id"]:
            return

        rows = await service.top_balances(guild_id)

        now = datetime.datetime.now(PARIS_TZ)
        embed = discord.Embed(title=f"{CURRENCY_EMOJI} {CURRENCY_NAME} Leaderboard", color=discord.Color.gold())
        embed.set_footer(text=f"Updated {now.strftime('%d/%m at %H:%M')}")
        if not rows:
            embed.description = "No wallets yet."
        else:
            lines = []
            for i, r in enumerate(rows, 1):
                user = self.bot.get_user(r["user_id"])
                name = user.display_name if user else f"<@{r['user_id']}>"
                lines.append(f"**#{i}** {name} — {r['balance']:,} {CURRENCY_EMOJI}")
            embed.description = "\n".join(lines)

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

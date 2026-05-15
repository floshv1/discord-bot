from __future__ import annotations

import datetime
from datetime import date
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")
MONTHS_EN = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _next_occurrence(day: int, month: int, today: date) -> date:
    try:
        d = date(today.year, month, day)
        if d < today:
            d = date(today.year + 1, month, day)
        return d
    except ValueError:
        return date(today.year + 1, month, day)


def _days_label(delta: int) -> str:
    if delta == 0:
        return "today 🎂"
    return f"in {delta} day{'s' if delta != 1 else ''}"


class BirthdayCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.birthday_ticker.start()

    async def cog_unload(self) -> None:
        self.birthday_ticker.cancel()

    @tasks.loop(minutes=1)
    async def birthday_ticker(self) -> None:
        now = datetime.datetime.now(PARIS_TZ)
        if now.hour == 0 and now.minute == 0:
            await self._update_upcoming_embed()
            await self._update_month_embed()
            await self._send_birthday_wishes(now)

    @birthday_ticker.before_loop
    async def before_birthday_ticker(self) -> None:
        await self.bot.wait_until_ready()
        await self._update_upcoming_embed()
        await self._update_month_embed()

    async def _update_upcoming_embed(self) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        if not config.birthday_channel_id:
            return

        pool = get_pool()
        guild_id = config.guild_id

        cfg = await pool.fetchrow(
            "SELECT channel_id, upcoming_message_id FROM birthday_config WHERE guild_id = $1",
            guild_id,
        )
        if not cfg or not cfg["upcoming_message_id"]:
            return

        rows = await pool.fetch(
            "SELECT user_id, username, day, month, year FROM birthdays WHERE guild_id = $1",
            guild_id,
        )

        today = datetime.datetime.now(PARIS_TZ).date()
        items = []
        for r in rows:
            next_bd = _next_occurrence(r["day"], r["month"], today)
            delta = (next_bd - today).days
            age = next_bd.year - r["year"]
            items.append((delta, r, age))
        items.sort(key=lambda x: x[0])

        embed = discord.Embed(title="🎉 Upcoming Birthdays", color=discord.Color.blue())
        if not items:
            embed.description = "No birthdays registered yet."
        else:
            for delta, r, age in items:
                embed.add_field(
                    name=r["username"],
                    value=f"{r['day']:02d}/{r['month']:02d} ({age} y/o) · {_days_label(delta)}",
                    inline=False,
                )

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(cfg["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(cfg["upcoming_message_id"])
            await msg.edit(embed=embed)
        except discord.NotFound:
            logger.warning("Birthday upcoming message not found.")

    async def _update_month_embed(self) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        if not config.birthday_channel_id:
            return

        pool = get_pool()
        guild_id = config.guild_id

        cfg = await pool.fetchrow(
            "SELECT channel_id, month_message_id FROM birthday_config WHERE guild_id = $1",
            guild_id,
        )
        if not cfg or not cfg["month_message_id"]:
            return

        now = datetime.datetime.now(PARIS_TZ)
        today = now.date()
        month = now.month
        month_name = MONTHS_EN[month - 1]

        rows = await pool.fetch(
            "SELECT user_id, username, day, month, year FROM birthdays WHERE guild_id = $1 AND month = $2 ORDER BY day",
            guild_id,
            month,
        )

        embed = discord.Embed(
            title=f"📅 {month_name} Birthdays",
            color=discord.Color.purple(),
        )
        if not rows:
            embed.description = f"No birthdays in {month_name}."
        else:
            for r in rows:
                next_bd = _next_occurrence(r["day"], r["month"], today)
                delta = (next_bd - today).days
                age = next_bd.year - r["year"]
                embed.add_field(
                    name=r["username"],
                    value=f"{r['day']:02d}/{r['month']:02d} ({age} y/o) · {_days_label(delta)}",
                    inline=False,
                )

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(cfg["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(cfg["month_message_id"])
            await msg.edit(embed=embed)
        except discord.NotFound:
            logger.warning("Birthday month message not found.")

    async def _send_birthday_wishes(self, now: datetime.datetime) -> None:
        config = self.bot.config  # type: ignore[attr-defined]
        if not config.birthday_announce_channel_id:
            return

        pool = get_pool()
        guild_id = config.guild_id

        rows = await pool.fetch(
            "SELECT user_id, year FROM birthdays WHERE guild_id = $1 AND day = $2 AND month = $3",
            guild_id,
            now.day,
            now.month,
        )
        if not rows:
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(config.birthday_announce_channel_id)
        if not channel:
            return

        wishes = "\n".join(f"- <@{r['user_id']}> turns {now.year - r['year']} today 🎈" for r in rows)
        await channel.send(f"🎂 Happy birthday to:\n{wishes}")

    birthday = app_commands.Group(name="birthday", description="Birthday commands.")

    @birthday.command(name="set", description="Register your birthday.")
    @app_commands.describe(day="Day (1-31)", month="Month (1-12)", year="Year (e.g. 2002)")
    async def birthday_set(self, interaction: discord.Interaction, day: int, month: int, year: int) -> None:
        if not (1 <= day <= 31):
            await interaction.response.send_message("Invalid day (1-31).", ephemeral=True)
            return
        if not (1 <= month <= 12):
            await interaction.response.send_message("Invalid month (1-12).", ephemeral=True)
            return
        if not (1900 <= year <= 2100):
            await interaction.response.send_message("Invalid year (1900-2100).", ephemeral=True)
            return

        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO birthdays (user_id, guild_id, username, day, month, year)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id) DO UPDATE
              SET username = EXCLUDED.username,
                  day = EXCLUDED.day,
                  month = EXCLUDED.month,
                  year = EXCLUDED.year,
                  updated_at = NOW()
            """,
            interaction.user.id,
            interaction.guild_id,
            interaction.user.name,
            day,
            month,
            year,
        )

        await interaction.response.send_message(f"🎉 Birthday saved: {day:02d}/{month:02d}/{year}!", ephemeral=True)
        await self._update_upcoming_embed()
        await self._update_month_embed()

    @birthday.command(name="delete", description="Delete your registered birthday.")
    async def birthday_delete(self, interaction: discord.Interaction) -> None:
        pool = get_pool()
        result = await pool.execute(
            "DELETE FROM birthdays WHERE user_id = $1",
            interaction.user.id,
        )
        if result == "DELETE 0":
            await interaction.response.send_message("You have no birthday registered.", ephemeral=True)
            return

        await interaction.response.send_message("✅ Birthday removed.", ephemeral=True)
        await self._update_upcoming_embed()
        await self._update_month_embed()

    @birthday.command(name="list", description="Show all upcoming birthdays.")
    async def birthday_list(self, interaction: discord.Interaction) -> None:
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT user_id, username, day, month, year FROM birthdays WHERE guild_id = $1",
            interaction.guild_id,
        )

        today = datetime.datetime.now(PARIS_TZ).date()
        items = []
        for r in rows:
            next_bd = _next_occurrence(r["day"], r["month"], today)
            delta = (next_bd - today).days
            age = next_bd.year - r["year"]
            items.append((delta, r, age))
        items.sort(key=lambda x: x[0])

        embed = discord.Embed(title="🎉 Upcoming Birthdays", color=discord.Color.blue())
        if not items:
            embed.description = "No birthdays registered yet."
        else:
            for delta, r, age in items:
                embed.add_field(
                    name=r["username"],
                    value=f"{r['day']:02d}/{r['month']:02d} ({age} y/o) · {_days_label(delta)}",
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BirthdayCog(bot))

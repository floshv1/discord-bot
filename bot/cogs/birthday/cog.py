from __future__ import annotations

import calendar
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


def _in_year(year: int, month: int, day: int) -> date:
    """The birthday as it falls in ``year``, clamped to the last day of the month.

    Feb 29 lands on Feb 28 in a non-leap year: a birthday that shifts by a day beats
    one that disappears for three years.
    """
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _next_occurrence(day: int, month: int, today: date) -> date:
    this_year = _in_year(today.year, month, day)
    if this_year >= today:
        return this_year
    return _in_year(today.year + 1, month, day)


async def claim_wishes_day(guild_id: int, today: date) -> bool:
    """Claim today's birthday announcement. True for exactly one caller, once per day.

    The wishes used to fire only inside the exact 00:00 minute, so a bot that was down or
    slow across midnight skipped the day entirely — and one that restarted *during* that
    minute could wish twice. Claiming the day in a single atomic statement makes the send
    idempotent, and lets a bot that comes up late still catch the day up.

    The very first claim for a guild only *seeds* the row and returns False: a bot being
    installed (or this table being migrated in) at 3pm should not immediately blast wishes
    at the channel. Announcements start from the next midnight.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO birthday_announcements (guild_id, last_wishes_on)
        VALUES ($1, $2)
        ON CONFLICT (guild_id) DO UPDATE SET last_wishes_on = EXCLUDED.last_wishes_on
          WHERE birthday_announcements.last_wishes_on < EXCLUDED.last_wishes_on
        RETURNING (xmax = 0) AS inserted
        """,
        guild_id,
        today,
    )
    if row is None:
        return False  # already announced today
    return not row["inserted"]  # a fresh row is a seed, not a day we owe wishes for


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
        guild_id = self.bot.config.guild_id  # type: ignore[attr-defined]
        # Whoever claims the day sends the wishes. Normally that's the 00:00 tick; if the
        # bot was down at midnight, it's the first tick after it comes back, so the day
        # isn't lost. Either way it happens once.
        if not await claim_wishes_day(guild_id, now.date()):
            return
        await self._update_upcoming_embed()
        await self._update_month_embed()
        await self._send_birthday_wishes(now)

    @birthday_ticker.before_loop
    async def before_birthday_ticker(self) -> None:
        await self.bot.wait_until_ready()
        await self._update_upcoming_embed()
        await self._update_month_embed()

    @birthday_ticker.error
    async def birthday_ticker_error(self, error: BaseException) -> None:
        logger.warning(f"birthday_ticker error (will retry next tick): {error}")

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
            "DELETE FROM birthdays WHERE user_id = $1 AND guild_id = $2",
            interaction.user.id,
            interaction.guild_id,
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

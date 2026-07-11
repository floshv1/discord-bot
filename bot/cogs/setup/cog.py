from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger

from bot.cogs.announce import service as announce_service
from bot.cogs.betting import service as betting_service
from bot.cogs.betting.cog import BettingCog
from bot.cogs.birthday.cog import MONTHS_EN, BirthdayCog
from bot.cogs.currency import service as currency_service
from bot.cogs.currency.cog import CurrencyCog
from bot.cogs.currency.embeds import build_panel_embed as build_currency_panel_embed
from bot.cogs.currency.views import CurrencyPanelView
from bot.cogs.queue import service as queue_service
from bot.cogs.queue.embeds import build_panel_embed
from bot.cogs.queue.views import PanelView
from bot.cogs.suggestions.cog import SetupView
from bot.cogs.voice.cog import VoiceCog
from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")


async def delete_setup_messages(
    client: discord.Client,
    channel_id: int | None,
    message_ids: list[int | None],
) -> int:
    """Delete the messages a previous /setup run posted. Returns how many went away.

    Without this, re-running /setup orphans the old panel: its buttons keep working (the
    views are persistent by custom_id), while the leaderboard beside it silently stops
    updating, because the DB now points at the new message. Best-effort — an admin who
    already cleared the channel by hand must not break setup.
    """
    if channel_id is None:
        return 0
    channel = client.get_channel(channel_id)
    if channel is None:
        return 0

    deleted = 0
    for message_id in message_ids:
        if message_id is None:
            continue
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
            deleted += 1
        except (discord.NotFound, discord.Forbidden):
            continue
        except discord.HTTPException as exc:
            logger.warning(f"Could not delete old setup message {message_id}: {exc}")
    return deleted


async def pin(message: discord.Message) -> None:
    """Pin a setup message, so the panel stays reachable once the channel scrolls.

    Everything called these "pinned" — the command descriptions, the docs — but nothing
    ever pinned them. Best-effort: missing Manage Messages, or a channel already at
    Discord's 50-pin cap, must not fail the whole setup.
    """
    try:
        await message.pin()
    except discord.HTTPException as exc:
        logger.warning(f"Could not pin setup message {message.id}: {exc}")


def status_line(name: str, command: str, channel_mention: str | None, missing_messages: int) -> str:
    """One line of /setup status.

    The middle case is the one an admin currently has no way to see: the config row exists,
    so the feature looks set up, but its message was deleted and it has silently stopped
    updating ever since.
    """
    if channel_mention is None:
        return f"❌ **{name}** — not set up. Run `{command}`."
    if missing_messages:
        return f"⚠️ **{name}** — {channel_mention}, but {missing_messages} message(s) are gone. Re-run `{command}`."
    return f"✅ **{name}** — {channel_mention}"


# name, table, message-id columns, the command that (re)creates it
FEATURES: list[tuple[str, str, list[str], str]] = [
    ("Voice leaderboard", "voice_leaderboard", ["weekly_message_id", "alltime_message_id"], "/setup voice"),
    ("Birthdays", "birthday_config", ["upcoming_message_id", "month_message_id"], "/setup birthday"),
    ("Currency", "currency_leaderboard", ["message_id", "panel_message_id", "pnl_message_id"], "/setup currency"),
    ("Queue", "queue_config", ["panel_message_id"], "/setup queue"),
    ("Suggestions", "suggestion_config", ["message_id"], "/setup suggestions"),
    ("Betting", "betting_config", [], "/setup betting"),
    ("Announcements", "announce_config", [], "/setup announce"),
    ("Reprimand", "reprimand_config", [], "/setup reprimand"),
]


async def _count_missing(client: discord.Client, channel_id: int, message_ids: list[int | None]) -> int:
    channel = client.get_channel(channel_id)
    if channel is None:
        return len([m for m in message_ids if m is not None])

    missing = 0
    for message_id in message_ids:
        if message_id is None:
            missing += 1
            continue
        try:
            await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            missing += 1
        except discord.HTTPException:
            pass  # transient — don't cry wolf
    return missing


async def _clear_previous(client: discord.Client, guild_id: int, table: str, columns: list[str]) -> None:
    """Look up what the last /setup wrote into ``table`` and delete those messages."""
    pool = get_pool()
    row = await pool.fetchrow(
        f"SELECT channel_id, {', '.join(columns)} FROM {table} WHERE guild_id = $1",  # noqa: S608 - names are literals
        guild_id,
    )
    if row:
        await delete_setup_messages(client, row["channel_id"], [row[c] for c in columns])


class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    setup = app_commands.Group(name="setup", description="Initialize bot features (moderators).")

    @setup.command(name="status", description="Show which features are configured, and which need attention.")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        pool = get_pool()

        lines = []
        for name, table, columns, command in FEATURES:
            selected = ", ".join(["channel_id", *columns])
            row = await pool.fetchrow(
                f"SELECT {selected} FROM {table} WHERE guild_id = $1",  # noqa: S608 - names are literals
                interaction.guild_id,
            )
            if row is None:
                lines.append(status_line(name, command, None, 0))
                continue
            channel = self.bot.get_channel(row["channel_id"])
            mention = channel.mention if channel else f"`#deleted-channel ({row['channel_id']})`"
            missing = await _count_missing(self.bot, row["channel_id"], [row[c] for c in columns])
            lines.append(status_line(name, command, mention, missing))

        embed = discord.Embed(
            title="🛠️ Setup status",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @setup.command(name="voice", description="Post the voice leaderboards (weekly + all-time) in a channel.")
    @app_commands.describe(channel="Channel that will host the two pinned leaderboards")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_voice(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True)
        await _clear_previous(
            self.bot, interaction.guild_id, "voice_leaderboard", ["weekly_message_id", "alltime_message_id"]
        )
        weekly_msg = await channel.send(
            embed=discord.Embed(title="🎙️ Voice — Last 7 Days", description="Loading...", color=discord.Color.blurple())
        )
        await pin(weekly_msg)
        alltime_msg = await channel.send(
            embed=discord.Embed(title="🏆 Voice — All Time", description="Loading...", color=discord.Color.gold())
        )
        await pin(alltime_msg)
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO voice_leaderboard (guild_id, channel_id, weekly_message_id, alltime_message_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id) DO UPDATE
              SET channel_id = EXCLUDED.channel_id,
                  weekly_message_id = EXCLUDED.weekly_message_id,
                  alltime_message_id = EXCLUDED.alltime_message_id
            """,
            interaction.guild_id,
            channel.id,
            weekly_msg.id,
            alltime_msg.id,
        )
        cog: VoiceCog | None = self.bot.cogs.get("VoiceCog")  # type: ignore[assignment]
        if cog:
            await cog._update_weekly_message()
            await cog._update_alltime_message()
        await interaction.followup.send(f"✅ Voice leaderboards posted in {channel.mention}.", ephemeral=True)

    @setup.command(name="birthday", description="Post the birthday embeds (upcoming + this month) in a channel.")
    @app_commands.describe(
        channel="Channel that will host the two pinned birthday embeds",
        announce_channel="Where to send the midnight birthday wishes (defaults to the same channel)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup_birthday(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        announce_channel: discord.TextChannel | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await _clear_previous(
            self.bot, interaction.guild_id, "birthday_config", ["upcoming_message_id", "month_message_id"]
        )
        now = datetime.datetime.now(PARIS_TZ)
        month_name = MONTHS_EN[now.month - 1]
        upcoming_msg = await channel.send(
            embed=discord.Embed(title="🎉 Upcoming Birthdays", description="Loading...", color=discord.Color.blue())
        )
        await pin(upcoming_msg)
        month_msg = await channel.send(
            embed=discord.Embed(
                title=f"📅 {month_name} Birthdays",
                description="Loading...",
                color=discord.Color.purple(),
            )
        )
        await pin(month_msg)
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO birthday_config
                (guild_id, channel_id, upcoming_message_id, month_message_id, announce_channel_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id) DO UPDATE
              SET channel_id = EXCLUDED.channel_id,
                  upcoming_message_id = EXCLUDED.upcoming_message_id,
                  month_message_id = EXCLUDED.month_message_id,
                  announce_channel_id = EXCLUDED.announce_channel_id
            """,
            interaction.guild_id,
            channel.id,
            upcoming_msg.id,
            month_msg.id,
            (announce_channel or channel).id,
        )
        cog: BirthdayCog | None = self.bot.cogs.get("BirthdayCog")  # type: ignore[assignment]
        if cog:
            await cog._update_upcoming_embed()
            await cog._update_month_embed()
        await interaction.followup.send(
            f"✅ Birthday embeds posted in {channel.mention}. "
            f"Wishes will go to {(announce_channel or channel).mention}.",
            ephemeral=True,
        )

    @setup.command(name="announce", description="Choisir le salon où /announce publie les annonces.")
    @app_commands.describe(channel="Salon qui recevra les annonces")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_announce(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        # No message is posted here, so there is nothing to clear or pin — just the channel.
        await announce_service.set_announce_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ Les annonces seront publiées dans {channel.mention}. Utilise `/announce` pour en écrire une.",
            ephemeral=True,
        )

    @setup.command(name="suggestions", description="Post the suggestion entry-point message in a channel.")
    @app_commands.describe(channel="Channel where suggestions will be collected")
    @app_commands.default_permissions(manage_channels=True)
    async def setup_suggestions(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True)
        await _clear_previous(self.bot, interaction.guild_id, "suggestion_config", ["message_id"])
        embed = discord.Embed(
            title="💡 Suggestions",
            description=(
                "Have an idea to make the server better?\n\n"
                "✨ **New Feature** — suggest something brand new\n"
                "🔧 **Improvement** — improve an existing feature"
            ),
            color=discord.Color.blurple(),
        )
        view = SetupView()
        msg = await channel.send(embed=embed, view=view)
        await pin(msg)
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO suggestion_config (guild_id, channel_id, message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id) DO UPDATE SET channel_id = $2, message_id = $3
            """,
            interaction.guild_id,
            channel.id,
            msg.id,
        )
        await interaction.followup.send(f"Suggestion channel set to {channel.mention}!", ephemeral=True)

    @setup.command(name="reprimand", description="Configure the Ennemi Public role and goulag channel.")
    @app_commands.describe(role="Role applied to reprimanded members", channel="Channel they're restricted to")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_reprimand(
        self, interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel
    ) -> None:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO reprimand_config (guild_id, role_id, channel_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id) DO UPDATE SET role_id = $2, channel_id = $3
            """,
            interaction.guild_id,
            role.id,
            channel.id,
        )
        await interaction.response.send_message(
            f"Reprimand configured: role {role.mention}, channel {channel.mention}.", ephemeral=True
        )

    @setup.command(name="currency", description="Post the FloshCoins panel + leaderboard, and fund every member.")
    @app_commands.describe(channel="Channel that will host the panel and pinned leaderboard")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_currency(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True)
        await _clear_previous(
            self.bot,
            interaction.guild_id,
            "currency_leaderboard",
            ["message_id", "panel_message_id", "pnl_message_id"],
        )

        # Give every current member their starting balance so the leaderboard isn't empty on day one.
        # Safe to re-run: backfill_wallets only creates wallets that don't exist yet, so nobody's
        # balance is reset by a second /setup currency.
        member_ids = [m.id for m in interaction.guild.members if not m.bot]
        funded = await currency_service.backfill_wallets(interaction.guild_id, member_ids)

        panel_view = CurrencyPanelView()
        panel_message = await channel.send(embed=build_currency_panel_embed(), view=panel_view)
        await pin(panel_message)
        self.bot.add_view(panel_view)
        leaderboard_message = await channel.send(
            embed=discord.Embed(title="🪙 FloshCoins Leaderboard", description="Loading...", color=discord.Color.gold())
        )
        await pin(leaderboard_message)
        pnl_message = await channel.send(
            embed=discord.Embed(title="📈 Bénéfices & pertes", description="Loading...", color=discord.Color.green())
        )
        await pin(pnl_message)
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO currency_leaderboard (guild_id, channel_id, message_id, panel_message_id, pnl_message_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id) DO UPDATE
              SET channel_id = EXCLUDED.channel_id,
                  message_id = EXCLUDED.message_id,
                  panel_message_id = EXCLUDED.panel_message_id,
                  pnl_message_id = EXCLUDED.pnl_message_id
            """,
            interaction.guild_id,
            channel.id,
            leaderboard_message.id,
            panel_message.id,
            pnl_message.id,
        )
        cog: CurrencyCog | None = self.bot.cogs.get("CurrencyCog")  # type: ignore[assignment]
        if cog:
            await cog._update_leaderboard_message()
            await cog._update_pnl_message()
        await interaction.followup.send(
            f"✅ Panneau + classement + bénéfices/pertes postés dans {channel.mention}. "
            f"**{funded}** nouveau(x) portefeuille(s) créé(s).",
            ephemeral=True,
        )

    @setup.command(name="betting", description="Set the channel where match betting cards are posted.")
    @app_commands.describe(channel="Channel that will host the match betting cards")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_betting(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        cog: BettingCog | None = self.bot.cogs.get("BettingCog")  # type: ignore[assignment]
        if cog and not cog.providers:
            await interaction.response.send_message(
                "❌ No betting provider is configured — set `FOOTBALL_DATA_API_KEY` and/or "
                "`PANDASCORE_API_KEY`, then restart the bot.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await betting_service.set_betting_channel(interaction.guild_id, channel.id)
        stats = await cog.poll_fixtures_now() if cog else {}

        # Report per provider so a dead feed is obvious, and so "already posted" doesn't
        # read as "broken" — both would otherwise show up as zero new markets.
        lines = [f"• `{name}` — {stat.summary()}" for name, stat in stats.items()]
        detail = "\n".join(lines) if lines else "_No providers configured._"
        await interaction.followup.send(
            f"✅ Betting channel set to {channel.mention}.\n\n{detail}",
            ephemeral=True,
        )

    @setup.command(name="queue", description="Post the game-queue control panel in a channel.")
    @app_commands.describe(channel="Channel that will host the queue panel and queue cards")
    @app_commands.default_permissions(manage_channels=True)
    async def setup_queue(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True)
        await _clear_previous(self.bot, interaction.guild_id, "queue_config", ["panel_message_id"])
        presets = await queue_service.list_presets(interaction.guild_id)
        view = PanelView(presets)
        msg = await channel.send(embed=build_panel_embed(), view=view)
        await pin(msg)
        self.bot.add_view(view)
        await queue_service.set_queue_config(interaction.guild_id, channel.id, msg.id)
        await interaction.followup.send(f"Queue panel posted in {channel.mention}!", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupCog(bot))

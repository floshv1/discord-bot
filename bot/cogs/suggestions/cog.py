from __future__ import annotations

import datetime

import discord
from discord.ext import commands

from bot.core.config import Config
from bot.db.client import get_pool

TYPE_LABELS: dict[str, str] = {
    "feature": "✨ New Feature",
    "improvement": "🔧 Improvement",
}

STATUS_COLORS: dict[str, discord.Color] = {
    "open": discord.Color.blurple(),
    "accepted": discord.Color.green(),
    "rejected": discord.Color.red(),
    "implemented": discord.Color.purple(),
}

STATUS_LABELS: dict[str, str] = {
    "open": "OPEN",
    "accepted": "ACCEPTED",
    "rejected": "REJECTED",
    "implemented": "IMPLEMENTED",
}


def build_suggestion_embed(
    number: int,
    type_: str,
    content: str,
    status: str,
    author_id: int,
    created_at: datetime.datetime,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"#{number} · {TYPE_LABELS.get(type_, type_)}",
        description=content,
        color=STATUS_COLORS.get(status, discord.Color.blurple()),
    )
    embed.add_field(name="Suggested by", value=f"<@{author_id}>", inline=True)
    embed.set_footer(text=f"[{STATUS_LABELS.get(status, status.upper())}] · {created_at.strftime('%d %b %Y')}")
    return embed


class VoteButton(discord.ui.Button):
    def __init__(self, suggestion_id: int, direction: int, count: int = 0) -> None:
        emoji = "👍" if direction == 1 else "👎"
        label = "vote_up" if direction == 1 else "vote_down"
        super().__init__(
            label=str(count),
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"suggestion:{label}:{suggestion_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        parts = self.custom_id.split(":")
        suggestion_id = int(parts[2])
        direction = 1 if parts[1] == "vote_up" else -1
        pool = get_pool()

        existing = await pool.fetchrow(
            "SELECT vote FROM suggestion_votes WHERE suggestion_id = $1 AND user_id = $2",
            suggestion_id,
            interaction.user.id,
        )

        if existing and existing["vote"] == direction:
            await pool.execute(
                "DELETE FROM suggestion_votes WHERE suggestion_id = $1 AND user_id = $2",
                suggestion_id,
                interaction.user.id,
            )
        else:
            await pool.execute(
                """
                INSERT INTO suggestion_votes (suggestion_id, user_id, vote)
                VALUES ($1, $2, $3)
                ON CONFLICT (suggestion_id, user_id) DO UPDATE SET vote = EXCLUDED.vote
                """,
                suggestion_id,
                interaction.user.id,
                direction,
            )

        suggestion = await pool.fetchrow(
            "SELECT number, type, content, status, author_id, created_at FROM suggestions WHERE id = $1",
            suggestion_id,
        )
        vote_up = await pool.fetchval(
            "SELECT COUNT(*) FROM suggestion_votes WHERE suggestion_id = $1 AND vote = 1",
            suggestion_id,
        )
        vote_down = await pool.fetchval(
            "SELECT COUNT(*) FROM suggestion_votes WHERE suggestion_id = $1 AND vote = -1",
            suggestion_id,
        )

        embed = build_suggestion_embed(
            number=suggestion["number"],
            type_=suggestion["type"],
            content=suggestion["content"],
            status=suggestion["status"],
            author_id=suggestion["author_id"],
            created_at=suggestion["created_at"],
        )
        view = SuggestionVoteView(suggestion_id, int(vote_up), int(vote_down))
        await interaction.response.edit_message(embed=embed, view=view)


class SuggestionVoteView(discord.ui.View):
    def __init__(self, suggestion_id: int, vote_up: int = 0, vote_down: int = 0) -> None:
        super().__init__(timeout=None)
        self.add_item(VoteButton(suggestion_id, 1, vote_up))
        self.add_item(VoteButton(suggestion_id, -1, vote_down))


class NewSuggestionButton(discord.ui.Button):
    def __init__(self, type_: str) -> None:
        label = "✨ New Feature" if type_ == "feature" else "🔧 Improvement"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"suggestion:{type_}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        type_ = self.custom_id.split(":")[1]
        await interaction.response.send_modal(SuggestionModal(type_))


class SetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(NewSuggestionButton("feature"))
        self.add_item(NewSuggestionButton("improvement"))


class SuggestionModal(discord.ui.Modal, title="Submit a Suggestion"):
    content_input = discord.ui.TextInput(
        label="Describe your suggestion",
        style=discord.TextStyle.paragraph,
        min_length=10,
        max_length=1000,
    )

    def __init__(self, type_: str) -> None:
        super().__init__()
        self.type_ = type_

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pool = get_pool()

        config_row = await pool.fetchrow(
            "SELECT channel_id FROM suggestion_config WHERE guild_id = $1",
            interaction.guild_id,
        )
        if not config_row:
            await interaction.response.send_message(
                "Suggestion system not configured. Ask an admin to run `/suggest setup`.",
                ephemeral=True,
            )
            return

        row = await pool.fetchrow(
            """
            WITH next_num AS (
                SELECT COALESCE(MAX(number), 0) + 1 AS n
                FROM suggestions
                WHERE guild_id = $1
            )
            INSERT INTO suggestions (number, guild_id, author_id, type, content)
            SELECT n, $1, $2, $3, $4 FROM next_num
            RETURNING id, number, created_at
            """,
            interaction.guild_id,
            interaction.user.id,
            self.type_,
            str(self.content_input),
        )
        number = row["number"]

        embed = build_suggestion_embed(
            number=number,
            type_=self.type_,
            content=str(self.content_input),
            status="open",
            author_id=interaction.user.id,
            created_at=row["created_at"],
        )
        view = SuggestionVoteView(row["id"], 0, 0)

        channel = interaction.guild.get_channel(config_row["channel_id"])
        if channel is None:
            await interaction.response.send_message(
                "The suggestion channel no longer exists. Ask an admin to run `/suggest setup` again.",
                ephemeral=True,
            )
            return
        msg = await channel.send(embed=embed, view=view)

        await pool.execute(
            "UPDATE suggestions SET message_id = $1 WHERE id = $2",
            msg.id,
            row["id"],
        )

        await interaction.response.send_message("Your suggestion has been submitted!", ephemeral=True)


class SuggestionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        pool = get_pool()
        self.bot.add_view(SetupView())

        rows = await pool.fetch(
            "SELECT id FROM suggestions WHERE guild_id = $1",
            self.config.guild_id,
        )
        for row in rows:
            self.bot.add_view(SuggestionVoteView(row["id"]))

    suggest = discord.app_commands.Group(name="suggest", description="Suggestion system commands.")

    @suggest.command(name="setup", description="Post the suggestion entry-point message in a channel.")
    @discord.app_commands.describe(channel="Channel where suggestions will be collected")
    @discord.app_commands.default_permissions(manage_channels=True)
    async def suggest_setup(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        pool = get_pool()

        embed = discord.Embed(
            title="💡 Suggestions",
            description=(
                "Have an idea to make the bot better?\n\n"
                "✨ **New Feature** — suggest something brand new\n"
                "🔧 **Improvement** — improve an existing feature"
            ),
            color=discord.Color.blurple(),
        )
        view = SetupView()
        msg = await channel.send(embed=embed, view=view)

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

        await interaction.response.send_message(f"Suggestion channel set to {channel.mention}!", ephemeral=True)

    @suggest.command(name="status", description="Update the status of a suggestion.")
    @discord.app_commands.describe(number="Suggestion number (e.g. 3)", status="New status")
    @discord.app_commands.choices(
        status=[
            discord.app_commands.Choice(name="Open", value="open"),
            discord.app_commands.Choice(name="Accepted", value="accepted"),
            discord.app_commands.Choice(name="Rejected", value="rejected"),
            discord.app_commands.Choice(name="Implemented", value="implemented"),
        ]
    )
    @discord.app_commands.default_permissions(kick_members=True)
    async def suggest_status(self, interaction: discord.Interaction, number: int, status: str) -> None:
        pool = get_pool()

        row = await pool.fetchrow(
            """
            SELECT s.id, s.type, s.content, s.author_id, s.created_at, s.message_id,
                   sc.channel_id
            FROM suggestions s
            LEFT JOIN suggestion_config sc ON sc.guild_id = s.guild_id
            WHERE s.guild_id = $1 AND s.number = $2
            """,
            interaction.guild_id,
            number,
        )

        if not row:
            await interaction.response.send_message(f"No suggestion #{number} found.", ephemeral=True)
            return

        await pool.execute(
            "UPDATE suggestions SET status = $1 WHERE id = $2",
            status,
            row["id"],
        )

        vote_up = await pool.fetchval(
            "SELECT COUNT(*) FROM suggestion_votes WHERE suggestion_id = $1 AND vote = 1",
            row["id"],
        )
        vote_down = await pool.fetchval(
            "SELECT COUNT(*) FROM suggestion_votes WHERE suggestion_id = $1 AND vote = -1",
            row["id"],
        )

        embed = build_suggestion_embed(
            number=number,
            type_=row["type"],
            content=row["content"],
            status=status,
            author_id=row["author_id"],
            created_at=row["created_at"],
        )
        view = SuggestionVoteView(row["id"], int(vote_up), int(vote_down))

        if row["channel_id"] and row["message_id"]:
            channel = interaction.guild.get_channel(row["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(row["message_id"])
                    await msg.edit(embed=embed, view=view)
                except discord.NotFound:
                    pass

        await interaction.response.send_message(f"Suggestion #{number} marked as **{status}**.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SuggestionCog(bot))

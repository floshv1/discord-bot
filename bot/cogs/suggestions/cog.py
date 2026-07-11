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


class VoteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"suggestion:(?P<direction>vote_up|vote_down):(?P<sid>\d+)",
):
    """A vote button that identifies its own suggestion from its custom_id.

    Dynamic so that *one* registration handles every suggestion. The cog used to
    `add_view` one view per suggestion row at boot — a query over the whole table, a store
    that grew forever, and still nothing for suggestions created after startup.
    """

    def __init__(self, suggestion_id: int, direction: int, count: int = 0) -> None:
        self.suggestion_id = suggestion_id
        self.direction = direction
        name = "vote_up" if direction == 1 else "vote_down"
        super().__init__(
            discord.ui.Button(
                label=str(count),
                emoji="👍" if direction == 1 else "👎",
                style=discord.ButtonStyle.secondary,
                custom_id=f"suggestion:{name}:{suggestion_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["sid"]), 1 if match["direction"] == "vote_up" else -1)

    async def callback(self, interaction: discord.Interaction) -> None:
        suggestion_id = self.suggestion_id
        direction = self.direction
        pool = get_pool()

        existing = await pool.fetchrow(
            "SELECT vote FROM suggestion_votes WHERE suggestion_id = $1 AND user_id = $2",
            suggestion_id,
            interaction.user.id,
        )

        # Clicking the side you already voted for takes the vote back.
        removed = bool(existing and existing["vote"] == direction)
        if removed:
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
        await interaction.followup.send("Vote removed." if removed else "Vote registered!", ephemeral=True)


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
                "Suggestion system not configured. Ask an admin to run `/setup suggestions`.",
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
                "The suggestion channel no longer exists. Ask an admin to run `/setup suggestions` again.",
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
        self.bot.add_view(SetupView())
        # One registration covers every suggestion — past, present, and any created later.
        self.bot.add_dynamic_items(VoteButton)

    suggest = discord.app_commands.Group(name="suggest", description="Suggestion system commands.")

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

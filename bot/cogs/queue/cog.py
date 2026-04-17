from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.core.config import Config
from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")

DEFAULT_PRESETS = [
    ("lol", 5),
    ("overwatch", 5),
]


def _parse_start_time(time_str: str) -> datetime.datetime | None:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            t = datetime.datetime.strptime(time_str, fmt).time()
            now_paris = datetime.datetime.now(tz=PARIS_TZ)
            dt = datetime.datetime.combine(now_paris.date(), t, tzinfo=PARIS_TZ)
            if dt <= now_paris:
                dt += datetime.timedelta(days=1)
            return dt.astimezone(datetime.UTC)
        except ValueError:
            continue
    return None


async def _game_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT name FROM game_presets WHERE guild_id = $1 AND name ILIKE $2 ORDER BY name LIMIT 10",
        interaction.guild_id,
        f"%{current}%",
    )
    return [app_commands.Choice(name=row["name"], value=row["name"]) for row in rows]


async def _fetch_queue_state(queue_id: int):
    pool = get_pool()
    queue = await pool.fetchrow(
        """
        SELECT gq.id, gq.status, gq.start_time, gq.creator_user_id, gp.name, gp.player_count
        FROM game_queues gq
        JOIN game_presets gp ON gp.id = gq.preset_id
        WHERE gq.id = $1
        """,
        queue_id,
    )
    members = (
        await pool.fetch(
            """
            SELECT user_id, in_lane, cant_attend
            FROM queue_members
            WHERE queue_id = $1
            ORDER BY cant_attend, in_lane, joined_at
            """,
            queue_id,
        )
        if queue
        else []
    )
    return queue, members


def _build_embed(queue, members: list) -> discord.Embed:
    main_members = [m for m in members if not m["in_lane"] and not m["cant_attend"]]
    waiting_members = [m for m in members if m["in_lane"] and not m["cant_attend"]]
    cant_members = [m for m in members if m["cant_attend"]]
    count = len(main_members)
    needed = queue["player_count"]
    status = queue["status"]
    name = queue["name"].upper()

    if status == "filled":
        title = f"✅ {name} — Lobby ready!"
        color = discord.Color.green()
    elif status == "done":
        title = f"🏁 {name} — Game over!"
        color = discord.Color.gold()
    elif status == "open":
        title = f"🎮 {name}"
        color = discord.Color.blurple()
    else:
        title = f"❌ {name} — Cancelled"
        color = discord.Color.dark_grey()

    embed = discord.Embed(title=title, color=color)

    player_list = "\n".join(f"<@{m['user_id']}>" for m in main_members) if main_members else "*No players yet*"
    embed.add_field(name=f"Players — {count}/{needed}", value=player_list, inline=True)

    if waiting_members:
        waiting_list = "\n".join(f"<@{m['user_id']}>" for m in waiting_members)
        embed.add_field(name=f"Waiting — {len(waiting_members)}", value=waiting_list, inline=True)

    if cant_members:
        cant_list = "\n".join(f"<@{m['user_id']}>" for m in cant_members)
        embed.add_field(name="Can't be here", value=cant_list, inline=True)

    if queue["start_time"]:
        ts = int(queue["start_time"].timestamp())
        paris_str = queue["start_time"].astimezone(PARIS_TZ).strftime("%H:%M")
        embed.add_field(name="Start time", value=f"<t:{ts}:t> ({paris_str} Paris)", inline=True)

    return embed


def _make_view(queue_id: int, status: str) -> QueueView:
    active = status in ("open", "filled")
    return QueueView(
        queue_id,
        join_disabled=(not active),
        cant_disabled=(not active),
        done_disabled=(not active),
    )


class JoinButton(discord.ui.Button):
    def __init__(self, queue_id: int, disabled: bool = False):
        super().__init__(
            label="Join",
            style=discord.ButtonStyle.green,
            emoji="✅",
            custom_id=f"queue:join:{queue_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        queue_id = int(self.custom_id.split(":")[2])
        pool = get_pool()

        queue, members = await _fetch_queue_state(queue_id)
        if not queue or queue["status"] not in ("open", "filled"):
            await interaction.response.send_message("This queue is no longer open.", ephemeral=True)
            return

        existing = next((m for m in members if m["user_id"] == interaction.user.id), None)
        if existing and not existing["cant_attend"]:
            await interaction.response.send_message("You are already in this queue.", ephemeral=True)
            return

        active_main = [m for m in members if not m["in_lane"] and not m["cant_attend"]]
        in_lane = len(active_main) >= queue["player_count"]

        if existing and existing["cant_attend"]:
            await pool.execute(
                "UPDATE queue_members SET cant_attend = FALSE, in_lane = $1 WHERE queue_id = $2 AND user_id = $3",
                in_lane,
                queue_id,
                interaction.user.id,
            )
        else:
            await pool.execute(
                "INSERT INTO queue_members (queue_id, user_id, in_lane) VALUES ($1, $2, $3)",
                queue_id,
                interaction.user.id,
                in_lane,
            )

        queue, members = await _fetch_queue_state(queue_id)
        main_members = [m for m in members if not m["in_lane"] and not m["cant_attend"]]
        if len(main_members) >= queue["player_count"] and queue["status"] == "open":
            await pool.execute(
                "UPDATE game_queues SET status = 'filled', filled_at = NOW() WHERE id = $1",
                queue_id,
            )
            queue, members = await _fetch_queue_state(queue_id)

        await interaction.response.edit_message(
            embed=_build_embed(queue, members),
            view=_make_view(queue_id, queue["status"]),
        )


class ICantButton(discord.ui.Button):
    def __init__(self, queue_id: int, disabled: bool = False):
        super().__init__(
            label="I can't",
            style=discord.ButtonStyle.secondary,
            emoji="🚫",
            custom_id=f"queue:cant:{queue_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        queue_id = int(self.custom_id.split(":")[2])
        pool = get_pool()

        queue, members = await _fetch_queue_state(queue_id)
        member = next((m for m in members if m["user_id"] == interaction.user.id), None)

        if not member or member["cant_attend"]:
            await interaction.response.send_message("You are not actively in this queue.", ephemeral=True)
            return

        was_in_main = not member["in_lane"]

        await pool.execute(
            "UPDATE queue_members SET cant_attend = TRUE WHERE queue_id = $1 AND user_id = $2",
            queue_id,
            interaction.user.id,
        )

        if was_in_main:
            await pool.execute(
                "UPDATE game_queues SET status = 'open', filled_at = NULL WHERE id = $1 AND status = 'filled'",
                queue_id,
            )
            promoted = await pool.fetchrow(
                """
                UPDATE queue_members SET in_lane = FALSE
                WHERE queue_id = $1 AND user_id = (
                    SELECT user_id FROM queue_members
                    WHERE queue_id = $1 AND in_lane = TRUE AND cant_attend = FALSE
                    ORDER BY joined_at LIMIT 1
                )
                RETURNING user_id
                """,
                queue_id,
            )
            if promoted:
                await interaction.channel.send(  # type: ignore[union-attr]
                    f"<@{promoted['user_id']}> was moved up into **{queue['name'].upper()}**! 🎉",
                    delete_after=30,
                )

        queue, members = await _fetch_queue_state(queue_id)
        main_members = [m for m in members if not m["in_lane"] and not m["cant_attend"]]
        if was_in_main and len(main_members) >= queue["player_count"]:
            await pool.execute(
                "UPDATE game_queues SET status = 'filled', filled_at = NOW() WHERE id = $1 AND status = 'open'",
                queue_id,
            )
            queue, members = await _fetch_queue_state(queue_id)

        await interaction.response.edit_message(
            embed=_build_embed(queue, members),
            view=_make_view(queue_id, queue["status"]),
        )


class DoneButton(discord.ui.Button):
    def __init__(self, queue_id: int, disabled: bool = False):
        super().__init__(
            label="Done",
            style=discord.ButtonStyle.success,
            emoji="🏁",
            custom_id=f"queue:done:{queue_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        queue_id = int(self.custom_id.split(":")[2])
        pool = get_pool()

        queue, _ = await _fetch_queue_state(queue_id)
        if not queue or queue["status"] not in ("open", "filled"):
            await interaction.response.send_message("This queue is no longer active.", ephemeral=True)
            return

        if queue["creator_user_id"] != interaction.user.id:
            await interaction.response.send_message("Only the queue creator can mark this as done.", ephemeral=True)
            return

        await pool.execute("UPDATE game_queues SET status = 'done' WHERE id = $1", queue_id)
        queue, members = await _fetch_queue_state(queue_id)

        await interaction.response.edit_message(
            embed=_build_embed(queue, members),
            view=_make_view(queue_id, "done"),
        )


class QueueView(discord.ui.View):
    def __init__(
        self,
        queue_id: int,
        join_disabled: bool = False,
        cant_disabled: bool = False,
        done_disabled: bool = False,
    ):
        super().__init__(timeout=None)
        self.add_item(JoinButton(queue_id, disabled=join_disabled))
        self.add_item(ICantButton(queue_id, disabled=cant_disabled))
        self.add_item(DoneButton(queue_id, disabled=done_disabled))


class QueueCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        pool = get_pool()
        for name, count in DEFAULT_PRESETS:
            await pool.execute(
                """
                INSERT INTO game_presets (guild_id, name, player_count)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, name) DO NOTHING
                """,
                self.config.guild_id,
                name,
                count,
            )
        active = await pool.fetch(
            "SELECT id, status FROM game_queues WHERE status IN ('open', 'filled') AND guild_id = $1",
            self.config.guild_id,
        )
        for row in active:
            self.bot.add_view(_make_view(row["id"], row["status"]))
        self.queue_ticker.start()

    async def cog_unload(self) -> None:
        self.queue_ticker.cancel()

    @tasks.loop(minutes=1)
    async def queue_ticker(self) -> None:
        pool = get_pool()
        now = datetime.datetime.now(tz=datetime.UTC)
        expiry_cutoff = now - datetime.timedelta(hours=1)

        expired = await pool.fetch(
            """
            UPDATE game_queues SET status = 'cancelled'
            WHERE status = 'open' AND created_at < $1
            RETURNING id, channel_id, message_id,
                      (SELECT name FROM game_presets WHERE id = preset_id) AS game_name
            """,
            expiry_cutoff,
        )
        for row in expired:
            if row["channel_id"] and row["message_id"]:
                channel = self.bot.get_channel(row["channel_id"])
                if channel:
                    try:
                        msg = await channel.fetch_message(row["message_id"])
                        queue, members = await _fetch_queue_state(row["id"])
                        await msg.edit(
                            embed=_build_embed(queue, members),
                            view=_make_view(row["id"], "cancelled"),
                        )
                    except discord.NotFound:
                        pass
            logger.info(f"Expired queue {row['id']} ({row['game_name']})")

        remind_cutoff = now + datetime.timedelta(minutes=10)
        to_remind = await pool.fetch(
            """
            UPDATE game_queues SET reminder_sent = TRUE
            WHERE status IN ('open', 'filled')
              AND start_time IS NOT NULL
              AND start_time <= $1 AND start_time > $2
              AND reminder_sent = FALSE
            RETURNING id, channel_id,
                      (SELECT name FROM game_presets WHERE id = preset_id) AS game_name
            """,
            remind_cutoff,
            now,
        )
        for row in to_remind:
            channel = self.bot.get_channel(row["channel_id"])
            if not channel:
                continue
            members = await pool.fetch(
                "SELECT user_id FROM queue_members WHERE queue_id = $1 AND in_lane = FALSE AND cant_attend = FALSE",
                row["id"],
            )
            mentions = " ".join(f"<@{m['user_id']}>" for m in members)
            await channel.send(f"⏰ **{row['game_name']}** starts in ~10 minutes! {mentions}")

    @queue_ticker.before_loop
    async def before_ticker(self) -> None:
        await self.bot.wait_until_ready()

    queue = app_commands.Group(name="queue", description="Game lobby queue commands.")

    @queue.command(name="join", description="Create or join a game queue.")
    @app_commands.describe(
        game="Game to queue for",
        start_time="Optional start time in Paris time, e.g. 21:00",
        notify="Users/roles to ping when the queue is created, e.g. @friend @TeamRole",
    )
    @app_commands.autocomplete(game=_game_autocomplete)
    async def queue_join(
        self,
        interaction: discord.Interaction,
        game: str,
        start_time: str | None = None,
        notify: str | None = None,
    ) -> None:
        pool = get_pool()

        preset = await pool.fetchrow(
            "SELECT id, player_count FROM game_presets WHERE guild_id = $1 AND name = $2",
            interaction.guild_id,
            game.lower(),
        )
        if not preset:
            await interaction.response.send_message(
                f"No preset found for **{game}**. Use `/queue add` to create one.", ephemeral=True
            )
            return

        parsed_time: datetime.datetime | None = None
        if start_time:
            parsed_time = _parse_start_time(start_time)
            if not parsed_time:
                await interaction.response.send_message("Invalid time format. Use HH:MM, e.g. `21:00`.", ephemeral=True)
                return

        existing = await pool.fetchrow(
            """
            SELECT id, channel_id, message_id FROM game_queues
            WHERE guild_id = $1 AND preset_id = $2 AND status IN ('open', 'filled')
            """,
            interaction.guild_id,
            preset["id"],
        )

        if existing:
            queue, members = await _fetch_queue_state(existing["id"])
            member = next((m for m in members if m["user_id"] == interaction.user.id), None)
            if member and not member["cant_attend"]:
                await interaction.response.send_message("You are already in this queue.", ephemeral=True)
                return

            active_main = [m for m in members if not m["in_lane"] and not m["cant_attend"]]
            in_lane = len(active_main) >= preset["player_count"]

            if member and member["cant_attend"]:
                await pool.execute(
                    "UPDATE queue_members SET cant_attend = FALSE, in_lane = $1 WHERE queue_id = $2 AND user_id = $3",
                    in_lane,
                    existing["id"],
                    interaction.user.id,
                )
            else:
                await pool.execute(
                    "INSERT INTO queue_members (queue_id, user_id, in_lane) VALUES ($1, $2, $3)",
                    existing["id"],
                    interaction.user.id,
                    in_lane,
                )

            queue, members = await _fetch_queue_state(existing["id"])
            main_members = [m for m in members if not m["in_lane"] and not m["cant_attend"]]
            if len(main_members) >= preset["player_count"] and queue["status"] == "open":
                await pool.execute(
                    "UPDATE game_queues SET status = 'filled', filled_at = NOW() WHERE id = $1",
                    existing["id"],
                )
                queue, members = await _fetch_queue_state(existing["id"])

            embed = _build_embed(queue, members)
            view = _make_view(existing["id"], queue["status"])

            if existing["message_id"] and existing["channel_id"]:
                channel = self.bot.get_channel(existing["channel_id"])
                if channel:
                    try:
                        msg = await channel.fetch_message(existing["message_id"])
                        await msg.edit(embed=embed, view=view)
                    except discord.NotFound:
                        pass

            await interaction.response.send_message("Joined the queue!", ephemeral=True)
            return

        queue_row = await pool.fetchrow(
            """
            INSERT INTO game_queues (guild_id, channel_id, preset_id, start_time, creator_user_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            interaction.guild_id,
            interaction.channel_id,
            preset["id"],
            parsed_time,
            interaction.user.id,
        )
        queue_id = queue_row["id"]
        await pool.execute(
            "INSERT INTO queue_members (queue_id, user_id, in_lane) VALUES ($1, $2, FALSE)",
            queue_id,
            interaction.user.id,
        )

        queue, members = await _fetch_queue_state(queue_id)
        embed = _build_embed(queue, members)
        view = _make_view(queue_id, "open")

        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        await pool.execute("UPDATE game_queues SET message_id = $1 WHERE id = $2", msg.id, queue_id)
        self.bot.add_view(view)

        if notify:
            await interaction.channel.send(  # type: ignore[union-attr]
                f"{notify} — Une queue **{game.upper()}** vient d'être créée ! Rejoins ici 👆",
                reference=msg,
            )

    @queue.command(name="list", description="List all open game queues.")
    async def queue_list(self, interaction: discord.Interaction) -> None:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT gq.id, gp.name, gp.player_count, gq.start_time,
                   COUNT(qm.id) FILTER (WHERE NOT qm.in_lane AND NOT qm.cant_attend) AS member_count
            FROM game_queues gq
            JOIN game_presets gp ON gp.id = gq.preset_id
            LEFT JOIN queue_members qm ON qm.queue_id = gq.id
            WHERE gq.guild_id = $1 AND gq.status IN ('open', 'filled')
            GROUP BY gq.id, gp.name, gp.player_count, gq.start_time
            ORDER BY gq.created_at
            """,
            interaction.guild_id,
        )
        if not rows:
            await interaction.response.send_message("No open queues right now.", ephemeral=True)
            return

        embed = discord.Embed(title="Open queues", color=discord.Color.blurple())
        for row in rows:
            value = f"{row['member_count']}/{row['player_count']} players"
            if row["start_time"]:
                paris_str = row["start_time"].astimezone(PARIS_TZ).strftime("%H:%M")
                ts = int(row["start_time"].timestamp())
                value += f" — <t:{ts}:t> ({paris_str} Paris)"
            embed.add_field(name=row["name"].upper(), value=value, inline=False)
        await interaction.response.send_message(embed=embed)

    @queue.command(name="add", description="Add a custom game preset.")
    @app_commands.describe(game="Game name", player_count="Number of players needed")
    @app_commands.default_permissions(kick_members=True)
    async def queue_add(self, interaction: discord.Interaction, game: str, player_count: int) -> None:
        if player_count < 2 or player_count > 100:
            await interaction.response.send_message("Player count must be between 2 and 100.", ephemeral=True)
            return
        pool = get_pool()
        try:
            await pool.execute(
                "INSERT INTO game_presets (guild_id, name, player_count) VALUES ($1, $2, $3)",
                interaction.guild_id,
                game.lower(),
                player_count,
            )
        except Exception:
            await interaction.response.send_message(f"A preset for **{game}** already exists.", ephemeral=True)
            return
        await interaction.response.send_message(f"Added preset **{game}** ({player_count} players).", ephemeral=True)

    @queue.command(name="cancel", description="Cancel the open queue for a game.")
    @app_commands.describe(game="Game queue to cancel")
    @app_commands.autocomplete(game=_game_autocomplete)
    async def queue_cancel(self, interaction: discord.Interaction, game: str) -> None:
        pool = get_pool()
        row = await pool.fetchrow(
            """
            SELECT gq.id, gq.channel_id, gq.message_id FROM game_queues gq
            JOIN game_presets gp ON gp.id = gq.preset_id
            WHERE gq.guild_id = $1 AND gp.name = $2 AND gq.status IN ('open', 'filled')
            """,
            interaction.guild_id,
            game.lower(),
        )
        if not row:
            await interaction.response.send_message(f"No active **{game}** queue found.", ephemeral=True)
            return

        await pool.execute("UPDATE game_queues SET status = 'cancelled' WHERE id = $1", row["id"])

        if row["channel_id"] and row["message_id"]:
            channel = self.bot.get_channel(row["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(row["message_id"])
                    queue, members = await _fetch_queue_state(row["id"])
                    await msg.edit(embed=_build_embed(queue, members), view=_make_view(row["id"], "cancelled"))
                except discord.NotFound:
                    pass

        await interaction.response.send_message(f"**{game}** queue cancelled.", ephemeral=True)

    @queue.command(name="remove", description="Remove a game preset.")
    @app_commands.describe(game="Game preset to remove")
    @app_commands.autocomplete(game=_game_autocomplete)
    @app_commands.default_permissions(kick_members=True)
    async def queue_remove(self, interaction: discord.Interaction, game: str) -> None:
        pool = get_pool()
        deleted = await pool.execute(
            "DELETE FROM game_presets WHERE guild_id = $1 AND name = $2",
            interaction.guild_id,
            game.lower(),
        )
        if deleted == "DELETE 0":
            await interaction.response.send_message(f"No preset found for **{game}**.", ephemeral=True)
            return
        await interaction.response.send_message(f"Removed preset **{game}**.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QueueCog(bot))

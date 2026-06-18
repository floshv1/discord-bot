from __future__ import annotations

import discord
from loguru import logger

from bot.cogs.queue import service
from bot.cogs.queue.embeds import build_queue_embed

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_card_view(queue_id: int, status: str) -> QueueCardView:
    active = status in ("open", "filled")
    return QueueCardView(queue_id, disabled=not active)


async def refresh_card_message(client: discord.Client, queue_id: int) -> None:
    """Re-render a queue card message in place from the current DB state."""
    queue, members = await service.fetch_queue_state(queue_id)
    if not queue or not queue["channel_id"] or not queue["message_id"]:
        return
    channel = client.get_channel(queue["channel_id"])
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(queue["message_id"])
        await msg.edit(
            embed=build_queue_embed(queue, members),
            view=make_card_view(queue_id, queue["status"]),
        )
    except discord.NotFound:
        pass


async def _create_and_post_queue(
    interaction: discord.Interaction,
    *,
    preset_id: int,
    preset_name: str,
    player_count: int,
    start_time,
) -> None:
    """Create a queue, post its card in the channel, and ping subscribers."""
    channel = interaction.channel
    queue_id = await service.create_queue(
        guild_id=interaction.guild_id,
        channel_id=channel.id,
        preset_id=preset_id,
        creator_user_id=interaction.user.id,
        player_count=player_count,
        start_time=start_time,
    )
    queue, members = await service.fetch_queue_state(queue_id)
    view = make_card_view(queue_id, queue["status"])
    card = await channel.send(embed=build_queue_embed(queue, members), view=view)
    await service.set_queue_message(queue_id, card.id)
    interaction.client.add_view(view)

    subscribers = await service.get_subscribers(interaction.guild_id, preset_id, interaction.user.id)
    if subscribers:
        mentions = " ".join(f"<@{uid}>" for uid in subscribers)
        await channel.send(
            f"{mentions} — a **{preset_name.upper()}** queue just opened! Join above 👆",
            reference=card,
        )


# ---------------------------------------------------------------------------
# Queue card (Join / Can't attend / Set time / Close)
# ---------------------------------------------------------------------------


class JoinButton(discord.ui.Button):
    def __init__(self, queue_id: int, disabled: bool = False) -> None:
        super().__init__(
            label="Join",
            style=discord.ButtonStyle.green,
            emoji="✅",
            custom_id=f"queue:join:{queue_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        queue_id = int(self.custom_id.split(":")[2])
        result = await service.join_queue(queue_id, interaction.user.id)
        if result == "closed":
            await interaction.response.send_message("This queue is no longer open.", ephemeral=True)
            return
        if result == "already_in":
            await interaction.response.send_message("You are already in this queue.", ephemeral=True)
            return
        queue, members = await service.fetch_queue_state(queue_id)
        await interaction.response.edit_message(
            embed=build_queue_embed(queue, members),
            view=make_card_view(queue_id, queue["status"]),
        )


class CantButton(discord.ui.Button):
    def __init__(self, queue_id: int, disabled: bool = False) -> None:
        super().__init__(
            label="Can't attend",
            style=discord.ButtonStyle.secondary,
            emoji="🚫",
            custom_id=f"queue:cant:{queue_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        queue_id = int(self.custom_id.split(":")[2])
        result, promoted = await service.mark_cant_attend(queue_id, interaction.user.id)
        if result == "not_in":
            await interaction.response.send_message("You are not actively in this queue.", ephemeral=True)
            return
        queue, members = await service.fetch_queue_state(queue_id)
        await interaction.response.edit_message(
            embed=build_queue_embed(queue, members),
            view=make_card_view(queue_id, queue["status"]),
        )
        if promoted:
            await interaction.channel.send(
                f"<@{promoted}> was moved up into **{queue['name'].upper()}**! 🎉",
                delete_after=30,
            )


class SetTimeButton(discord.ui.Button):
    def __init__(self, queue_id: int, disabled: bool = False) -> None:
        super().__init__(
            label="Set time",
            style=discord.ButtonStyle.secondary,
            emoji="⏰",
            custom_id=f"queue:settime:{queue_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        queue_id = int(self.custom_id.split(":")[2])
        queue, _ = await service.fetch_queue_state(queue_id)
        if not queue or queue["status"] not in ("open", "filled"):
            await interaction.response.send_message("This queue is no longer active.", ephemeral=True)
            return
        if queue["creator_user_id"] != interaction.user.id:
            await interaction.response.send_message("Only the host can set the start time.", ephemeral=True)
            return
        await interaction.response.send_modal(SetTimeModal(queue_id))


class CloseButton(discord.ui.Button):
    def __init__(self, queue_id: int, disabled: bool = False) -> None:
        super().__init__(
            label="Close",
            style=discord.ButtonStyle.danger,
            emoji="🏁",
            custom_id=f"queue:close:{queue_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        queue_id = int(self.custom_id.split(":")[2])
        queue, _ = await service.fetch_queue_state(queue_id)
        if not queue or queue["status"] not in ("open", "filled"):
            await interaction.response.send_message("This queue is no longer active.", ephemeral=True)
            return
        can_close = await service.is_member(queue_id, interaction.user.id) or (
            interaction.user.guild_permissions.manage_messages
        )
        if not can_close:
            await interaction.response.send_message("Only someone in the queue can close it.", ephemeral=True)
            return
        await service.close_queue(queue_id)
        queue, members = await service.fetch_queue_state(queue_id)
        await interaction.response.edit_message(
            embed=build_queue_embed(queue, members),
            view=make_card_view(queue_id, "done"),
        )
        try:
            await interaction.message.delete(delay=15)
        except discord.HTTPException:
            pass


class QueueCardView(discord.ui.View):
    def __init__(self, queue_id: int, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.add_item(JoinButton(queue_id, disabled=disabled))
        self.add_item(CantButton(queue_id, disabled=disabled))
        self.add_item(SetTimeButton(queue_id, disabled=disabled))
        self.add_item(CloseButton(queue_id, disabled=disabled))


class SetTimeModal(discord.ui.Modal, title="Set start time"):
    time_input = discord.ui.TextInput(
        label="Start time (Paris), e.g. 21:00",
        placeholder="21:00",
        required=True,
        max_length=8,
    )

    def __init__(self, queue_id: int) -> None:
        super().__init__()
        self.queue_id = queue_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parsed = service.parse_start_time(str(self.time_input))
        if not parsed:
            await interaction.response.send_message("Invalid time. Use HH:MM, e.g. `21:00`.", ephemeral=True)
            return
        await service.set_start_time(self.queue_id, parsed)
        await refresh_card_message(interaction.client, self.queue_id)
        await interaction.response.send_message("✅ Start time updated.", ephemeral=True)


# ---------------------------------------------------------------------------
# Size picker (shown when a game button is tapped)
# ---------------------------------------------------------------------------


class SizeSelect(discord.ui.Select):
    def __init__(self, preset_id: int, preset_name: str, default_count: int) -> None:
        options = [
            discord.SelectOption(label="Duo (2 players)", value="2", emoji="👥"),
        ]
        if default_count != 2:
            options.append(
                discord.SelectOption(label=f"Full team ({default_count} players)", value=str(default_count), emoji="🎯")
            )
        options.append(discord.SelectOption(label="Custom size…", value="custom", emoji="✏️"))
        super().__init__(placeholder="Choose a queue size…", options=options, min_values=1, max_values=1)
        self.preset_id = preset_id
        self.preset_name = preset_name

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = self.values[0]
        if choice == "custom":
            await interaction.response.send_modal(CustomSizeModal(self.preset_id, self.preset_name))
            return
        await interaction.response.edit_message(content="✅ Creating your queue…", view=None)
        await _create_and_post_queue(
            interaction,
            preset_id=self.preset_id,
            preset_name=self.preset_name,
            player_count=int(choice),
            start_time=None,
        )
        await interaction.edit_original_response(content="✅ Queue created — see the channel 👇")


class SizeSelectView(discord.ui.View):
    def __init__(self, preset_id: int, preset_name: str, default_count: int) -> None:
        super().__init__(timeout=180)
        self.add_item(SizeSelect(preset_id, preset_name, default_count))


class CustomSizeModal(discord.ui.Modal, title="Custom queue"):
    size_input = discord.ui.TextInput(label="Number of players", placeholder="3", required=True, max_length=3)
    time_input = discord.ui.TextInput(
        label="Start time (Paris), optional — e.g. 21:00",
        required=False,
        max_length=8,
    )

    def __init__(self, preset_id: int, preset_name: str) -> None:
        super().__init__()
        self.preset_id = preset_id
        self.preset_name = preset_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            count = int(str(self.size_input))
        except ValueError:
            await interaction.response.send_message("Player count must be a number.", ephemeral=True)
            return
        if count < 2 or count > 100:
            await interaction.response.send_message("Player count must be between 2 and 100.", ephemeral=True)
            return
        start_time = None
        if str(self.time_input).strip():
            start_time = service.parse_start_time(str(self.time_input))
            if not start_time:
                await interaction.response.send_message("Invalid time. Use HH:MM, e.g. `21:00`.", ephemeral=True)
                return
        await interaction.response.send_message("✅ Queue created — see the channel 👇", ephemeral=True)
        await _create_and_post_queue(
            interaction,
            preset_id=self.preset_id,
            preset_name=self.preset_name,
            player_count=count,
            start_time=start_time,
        )


# ---------------------------------------------------------------------------
# Panel (persistent) — game buttons + Other + Subscriptions
# ---------------------------------------------------------------------------


class GameButton(discord.ui.Button):
    def __init__(self, preset_id: int, preset_name: str) -> None:
        super().__init__(
            label=preset_name.upper(),
            style=discord.ButtonStyle.primary,
            emoji="🎮",
            custom_id=f"queuepanel:game:{preset_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        preset_id = int(self.custom_id.split(":")[2])
        preset = await service.get_preset(preset_id)
        if not preset:
            await interaction.response.send_message("That game preset no longer exists.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"How many players for **{preset['name'].upper()}**?",
            view=SizeSelectView(preset_id, preset["name"], preset["player_count"]),
            ephemeral=True,
        )


class OtherGameButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Other game",
            style=discord.ButtonStyle.secondary,
            emoji="➕",
            custom_id="queuepanel:other",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(OtherGameModal())


class SubscriptionsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Subscriptions",
            style=discord.ButtonStyle.secondary,
            emoji="🔔",
            custom_id="queuepanel:subs",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        presets = await service.list_presets(interaction.guild_id)
        if not presets:
            await interaction.response.send_message("No games to subscribe to yet.", ephemeral=True)
            return
        current = await service.get_subscriptions(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            "Pick the games you want to be pinged for:",
            view=SubscriptionSelectView(presets, current),
            ephemeral=True,
        )


class PanelView(discord.ui.View):
    # A view holds at most 25 components; reserve 2 for "Other game" + "Subscriptions".
    MAX_GAME_BUTTONS = 23

    def __init__(self, presets) -> None:
        super().__init__(timeout=None)
        for preset in presets[: self.MAX_GAME_BUTTONS]:
            self.add_item(GameButton(preset["id"], preset["name"]))
        self.add_item(OtherGameButton())
        self.add_item(SubscriptionsButton())


class OtherGameModal(discord.ui.Modal, title="New game queue"):
    game_input = discord.ui.TextInput(label="Game name", placeholder="valorant", required=True, max_length=40)
    size_input = discord.ui.TextInput(label="Number of players", placeholder="5", required=True, max_length=3)
    time_input = discord.ui.TextInput(
        label="Start time (Paris), optional — e.g. 21:00",
        required=False,
        max_length=8,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            count = int(str(self.size_input))
        except ValueError:
            await interaction.response.send_message("Player count must be a number.", ephemeral=True)
            return
        if count < 2 or count > 100:
            await interaction.response.send_message("Player count must be between 2 and 100.", ephemeral=True)
            return
        start_time = None
        if str(self.time_input).strip():
            start_time = service.parse_start_time(str(self.time_input))
            if not start_time:
                await interaction.response.send_message("Invalid time. Use HH:MM, e.g. `21:00`.", ephemeral=True)
                return

        name = str(self.game_input).strip().lower()
        preset = await service.upsert_preset(interaction.guild_id, name, count)
        await interaction.response.send_message("✅ Queue created — see the channel 👇", ephemeral=True)
        await _create_and_post_queue(
            interaction,
            preset_id=preset["id"],
            preset_name=preset["name"],
            player_count=count,
            start_time=start_time,
        )
        cog = interaction.client.get_cog("QueueCog")
        if cog:
            try:
                await cog.refresh_panel()
            except Exception as exc:  # pragma: no cover - best-effort panel refresh
                logger.warning(f"Failed to refresh queue panel: {exc}")


class SubscriptionSelect(discord.ui.Select):
    def __init__(self, presets, current: set[int]) -> None:
        # A select menu supports at most 25 options.
        options = [
            discord.SelectOption(
                label=p["name"].upper(),
                value=str(p["id"]),
                default=p["id"] in current,
            )
            for p in presets[:25]
        ]
        super().__init__(
            placeholder="Select the games to be pinged for…",
            options=options,
            min_values=0,
            max_values=len(options),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        preset_ids = {int(v) for v in self.values}
        await service.set_subscriptions(interaction.guild_id, interaction.user.id, preset_ids)
        await interaction.response.edit_message(content="✅ Subscriptions updated!", view=None)


class SubscriptionSelectView(discord.ui.View):
    def __init__(self, presets, current: set[int]) -> None:
        super().__init__(timeout=180)
        self.add_item(SubscriptionSelect(presets, current))

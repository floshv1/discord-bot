from __future__ import annotations

from collections.abc import Mapping, Sequence
from zoneinfo import ZoneInfo

import discord

PARIS_TZ = ZoneInfo("Europe/Paris")


def build_panel_embed() -> discord.Embed:
    """The persistent control-panel message posted in the queue channel."""
    embed = discord.Embed(
        title="🎮 Game Queues",
        description=(
            "Want to play? Open a queue and others can join.\n\n"
            "**•** Tap a game below, pick a size (Duo / Flex / Custom).\n"
            "**•** The first person to join is your duo partner.\n"
            "**•** Tap 🔔 **Subscriptions** to get pinged for the games you care about."
        ),
        color=discord.Color.blurple(),
    )
    return embed


def build_queue_embed(queue: Mapping, members: Sequence[Mapping]) -> discord.Embed:
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
        embed.add_field(name="Can't attend", value=cant_list, inline=True)

    if queue["start_time"]:
        ts = int(queue["start_time"].timestamp())
        paris_str = queue["start_time"].astimezone(PARIS_TZ).strftime("%H:%M")
        embed.add_field(name="Start time", value=f"<t:{ts}:t> ({paris_str} Paris)", inline=True)

    if queue.get("creator_user_id"):
        embed.add_field(name="Host", value=f"<@{queue['creator_user_id']}>", inline=True)

    return embed

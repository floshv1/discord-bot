from __future__ import annotations

from typing import Any

import discord

from bot.cogs.music.utils import format_progress_bar

IDLE_TEXT = "Nothing playing — use `/play` to start something."
EMPTY_HISTORY_TEXT = "Nothing played yet."

# Why the player stopped. These land in the idle card's footer instead of being posted as
# their own message: the point of the pinned card is that the channel stays quiet.
REASON_INACTIVITY = "Left — inactivity"
REASON_EVERYONE_LEFT = "Everyone left the voice channel"


def stopped_by(name: str) -> str:
    return f"Stopped by {name}"


def build_now_playing_embed(track: Any, player: Any) -> discord.Embed:
    requester_id = getattr(track.extras, "requester", None)
    req = f"<@{requester_id}>" if requester_id else "Autoplay"
    embed = discord.Embed(
        title="Now Playing",
        description=f"[{track.title}]({track.uri})",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="Progress",
        value=format_progress_bar(player.position, track.length),
        inline=False,
    )
    embed.add_field(name="Artist", value=track.author or "—", inline=True)
    album_name = getattr(track.album, "name", None)
    if album_name:
        embed.add_field(name="Album", value=album_name, inline=True)
    embed.add_field(name="Requested by", value=req, inline=True)

    queue_list = list(player.queue)
    if queue_list:
        next_track = queue_list[0]
        embed.add_field(name="Next song", value=f"[{next_track.title}]({next_track.uri})", inline=True)
    elif player.autoplay_enabled:
        auto_list = list(player.auto_queue)
        if auto_list:
            next_auto = auto_list[0]
            embed.add_field(name="Next song", value=f"🔀 [{next_auto.title}]({next_auto.uri})", inline=True)
        else:
            embed.add_field(name="Next song", value="🔀 Autoplay", inline=True)

    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    return embed


def build_idle_embed(reason: str | None = None) -> discord.Embed:
    """The resting state of the pinned card.

    ``reason`` is why playback ended — it replaces the message we used to post into the
    channel ("Left the voice channel due to inactivity.", "Everyone left — disconnecting.").
    It is absent at boot, because a restart is not a reason anybody needs to read about.
    """
    embed = discord.Embed(
        title="Now Playing",
        description=IDLE_TEXT,
        color=discord.Color.dark_grey(),
    )
    if reason:
        embed.set_footer(text=reason)
    return embed


def build_history_embed(rows: list[Any]) -> discord.Embed:
    """The pinned play history, newest first.

    Timestamps are rendered as Discord's relative form so the card stays correct between
    redraws — the history is only redrawn when a track starts, and "2 minutes ago" baked
    into text would be a lie an hour later.
    """
    embed = discord.Embed(title="🎶 Recently played", color=discord.Color.blurple())

    lines: list[str] = []
    for row in rows:
        title = row["track_title"]
        uri = row["track_uri"]
        name = f"[{title}]({uri})" if uri else f"**{title}**"
        author = row["track_author"]
        who = f"<@{row['requester_id']}>" if row["requester_id"] else "🔀 Autoplay"
        when = f"<t:{int(row['played_at'].timestamp())}:R>"
        lines.append(f"{name}{f' — {author}' if author else ''} · {who} · {when}")

    embed.description = "\n".join(lines) if lines else EMPTY_HISTORY_TEXT
    return embed

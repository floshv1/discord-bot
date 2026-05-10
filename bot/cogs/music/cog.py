from __future__ import annotations

from typing import cast

import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from loguru import logger

from bot.cogs.music.player import MusicPlayer
from bot.core.config import Config


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _build_queue_embed(player: MusicPlayer) -> discord.Embed:
    embed = discord.Embed(title="Queue", color=discord.Color.blurple())
    lines: list[str] = []

    if player.current:
        lines.append(
            f"**Now playing:** [{player.current.title}]({player.current.uri}) `{_fmt_ms(player.current.length)}`"
        )

    queue_tracks = list(player.queue)
    for i, track in enumerate(queue_tracks[:10], start=1):
        requester_id = getattr(track.extras, "requester", None)
        req = f"<@{requester_id}>" if requester_id else "—"
        lines.append(f"`{i}.` [{track.title}]({track.uri}) `{_fmt_ms(track.length)}` — {req}")

    if player.queue.count > 10:
        lines.append(f"*…and {player.queue.count - 10} more*")

    embed.description = "\n".join(lines) if lines else "Queue is empty."
    total_ms = sum(t.length for t in queue_tracks)
    embed.set_footer(text=f"{player.queue.count} track(s) in queue — Total: {_fmt_ms(total_ms)}")
    return embed


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        node = wavelink.Node(uri=self.config.lavalink_uri, password=self.config.lavalink_password)
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot)
            logger.info("Connected to Lavalink node at %s.", self.config.lavalink_uri)
        except Exception:
            logger.exception("Could not connect to Lavalink — music commands will be unavailable.")

    def _player(self, interaction: discord.Interaction) -> MusicPlayer | None:
        return cast("MusicPlayer | None", interaction.guild.voice_client)

    @app_commands.command(name="play", description="Play a song or playlist from YouTube or Spotify.")
    @app_commands.describe(query="Song name, YouTube URL, or Spotify URL")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("You need to be in a voice channel first.", ephemeral=True)
            return

        player = self._player(interaction)

        if player and player.channel != interaction.user.voice.channel:
            await interaction.followup.send("I'm already playing in a different voice channel.", ephemeral=True)
            return

        if not player:
            try:
                player = await interaction.user.voice.channel.connect(cls=MusicPlayer)
            except discord.ClientException:
                await interaction.followup.send("Could not join your voice channel.", ephemeral=True)
                return
            player.text_channel = interaction.channel

        try:
            tracks: wavelink.Search = await wavelink.Playable.search(query)
        except wavelink.LavalinkException as e:
            await interaction.followup.send(f"Could not load track: {e}", ephemeral=True)
            return

        if not tracks:
            await interaction.followup.send(f"No results found for `{query}`.", ephemeral=True)
            if not player.playing and player.queue.is_empty:
                await player.disconnect()
            return

        if isinstance(tracks, wavelink.Playlist):
            for track in tracks:
                track.extras.requester = interaction.user.id
            added = await player.queue.put_wait(tracks)
            msg = f"Queued **{tracks.name}** — {added} tracks."
        else:
            track = tracks[0]
            track.extras.requester = interaction.user.id
            await player.queue.put_wait(track)
            msg = f"Queued **{track.title}** `{_fmt_ms(track.length)}`"

        if not player.playing:
            await player.play(player.queue.get())

        await interaction.followup.send(msg)

    @app_commands.command(name="playnext", description="Insert a song right after the current track.")
    @app_commands.describe(query="Song name, YouTube URL, or Spotify URL")
    async def playnext(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("You need to be in a voice channel first.", ephemeral=True)
            return

        player = self._player(interaction)
        if not player:
            await interaction.followup.send("Nothing is currently playing.", ephemeral=True)
            return
        if player.channel != interaction.user.voice.channel:
            await interaction.followup.send("Join my voice channel first.", ephemeral=True)
            return

        try:
            tracks: wavelink.Search = await wavelink.Playable.search(query)
        except wavelink.LavalinkException as e:
            await interaction.followup.send(f"Could not load track: {e}", ephemeral=True)
            return

        if not tracks:
            await interaction.followup.send(f"No results found for `{query}`.", ephemeral=True)
            return

        track = tracks.tracks[0] if isinstance(tracks, wavelink.Playlist) else tracks[0]
        track.extras.requester = interaction.user.id
        player.queue.put_at(0, track)
        await interaction.followup.send(f"**{track.title}** will play next.")

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
            return
        player = self._player(interaction)
        if not player or player.channel != interaction.user.voice.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        await interaction.response.send_message("Skipped.")
        await player.stop(force=True)

    @app_commands.command(name="stop", description="Stop playback, clear the queue, and disconnect.")
    async def stop(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
            return
        player = self._player(interaction)
        if not player or player.channel != interaction.user.voice.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        player.queue.clear()
        await interaction.response.send_message("Stopped and disconnected.")
        await player.disconnect()

    @app_commands.command(name="list", description="Show the current queue.")
    async def list_queue(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or (not player.current and player.queue.is_empty):
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await interaction.response.send_message(embed=_build_queue_embed(player))

    @app_commands.command(name="remove", description="Remove a track from the queue by position.")
    @app_commands.describe(position="Position in the queue (1 = next track)")
    async def remove(self, interaction: discord.Interaction, position: int) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
            return
        player = self._player(interaction)
        if not player or player.channel != interaction.user.voice.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        if position < 1 or position > player.queue.count:
            await interaction.response.send_message(
                f"Invalid position — queue only has {player.queue.count} track(s).", ephemeral=True
            )
            return
        track = player.queue.peek(position - 1)
        player.queue.delete(position - 1)
        await interaction.response.send_message(f"Removed **{track.title}** from the queue.")

    @app_commands.command(name="autoplay", description="Toggle autoplay (YouTube recommendations when queue ends).")
    async def autoplay_toggle(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
            return
        player = self._player(interaction)
        if not player or player.channel != interaction.user.voice.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        player.autoplay_enabled = not player.autoplay_enabled
        if player.autoplay_enabled:
            player.autoplay = wavelink.AutoPlayMode.enabled
            await interaction.response.send_message(
                "Autoplay **enabled** — I'll keep the music going with recommendations."
            )
        else:
            player.autoplay = wavelink.AutoPlayMode.partial
            await interaction.response.send_message("Autoplay **disabled** — I'll stop when the queue is empty.")

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = cast("MusicPlayer | None", payload.player)
        if not isinstance(player, MusicPlayer) or not player.text_channel:
            return
        track = payload.track
        requester_id = getattr(track.extras, "requester", None)
        req = f"<@{requester_id}>" if requester_id else "Autoplay"
        embed = discord.Embed(
            title="Now Playing",
            description=f"[{track.title}]({track.uri})",
            color=discord.Color.green(),
        )
        embed.add_field(name="Artist", value=track.author or "—", inline=True)
        album_name = getattr(track.album, "name", None)
        if album_name:
            embed.add_field(name="Album", value=album_name, inline=True)
        embed.add_field(name="Duration", value=_fmt_ms(track.length), inline=True)
        embed.add_field(name="Requested by", value=req, inline=True)
        if track.artwork:
            embed.set_thumbnail(url=track.artwork)
        try:
            await player.text_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        if not isinstance(player, MusicPlayer):
            return
        if player.text_channel:
            try:
                await player.text_channel.send("Left the voice channel due to inactivity.")
            except discord.HTTPException:
                pass
        await player.disconnect()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return
        player = cast("MusicPlayer | None", member.guild.voice_client)
        if not isinstance(player, MusicPlayer):
            return
        if before.channel is None or before.channel != player.channel:
            return
        if not any(not m.bot for m in player.channel.members):
            if player.text_channel:
                try:
                    await player.text_channel.send("Everyone left — disconnecting.")
                except discord.HTTPException:
                    pass
            player.queue.clear()
            await player.disconnect()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))

from __future__ import annotations

import asyncio
from typing import cast

import aiohttp
import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from loguru import logger

from bot.cogs.music import db_sync
from bot.cogs.music.player import MusicPlayer
from bot.cogs.music.utils import (
    _fmt_ms,
    calculate_eta,
    format_progress_bar,
    is_filtered_autoplay_track,
    titles_similar,
)
from bot.cogs.music.views import LyricsView, NowPlayingView, QueueView
from bot.core.config import Config
from bot.db.client import get_pool


async def _delete_after(msg: discord.Message, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except discord.HTTPException:
        pass


async def _disable_now_playing(player: MusicPlayer) -> None:
    if player.now_playing_message:
        try:
            await player.now_playing_message.edit(view=None)
        except discord.HTTPException:
            pass
        player.now_playing_message = None


def _build_now_playing_embed(track: wavelink.Playable, player: MusicPlayer) -> discord.Embed:
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
        embed.add_field(name="Prochain", value=f"[{next_track.title}]({next_track.uri})", inline=True)
    elif player.autoplay_enabled:
        embed.add_field(name="Prochain", value="Autoplay", inline=True)

    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    return embed


async def _cancel_progress_task(player: MusicPlayer) -> None:
    if player._progress_task and not player._progress_task.done():
        player._progress_task.cancel()
        try:
            await player._progress_task
        except asyncio.CancelledError:
            pass
    player._progress_task = None


async def _progress_loop(player: MusicPlayer) -> None:
    try:
        while True:
            await asyncio.sleep(5)
            if not player.current or not player.now_playing_message:
                break
            embed = _build_now_playing_embed(player.current, player)
            try:
                await player.now_playing_message.edit(embed=embed)
            except discord.NotFound:
                break
            except discord.HTTPException:
                pass
    except asyncio.CancelledError:
        pass


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]

    async def cog_unload(self) -> None:
        if hasattr(self, "_cmd_task"):
            self._cmd_task.cancel()
            try:
                await self._cmd_task
            except asyncio.CancelledError:
                pass

    async def _command_poll_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            try:
                pool = get_pool()
                for guild in self.bot.guilds:
                    player = cast("MusicPlayer | None", guild.voice_client)
                    if isinstance(player, MusicPlayer):
                        await db_sync.poll_and_execute_commands(pool, player)
            except Exception:
                logger.exception("Error in music command poll loop.")

    async def cog_load(self) -> None:
        node = wavelink.Node(uri=self.config.lavalink_uri, password=self.config.lavalink_password)
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot)
            logger.info("Connected to Lavalink node at {}.", self.config.lavalink_uri)
        except Exception:
            logger.exception("Could not connect to Lavalink — music commands will be unavailable.")

        self._cmd_task = asyncio.create_task(self._command_poll_loop())

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

            duplicate_warning = ""
            if player.playing:
                all_queued = list(player.queue)
                candidates = [(i + 1, t) for i, t in enumerate(all_queued) if titles_similar(track.title, t.title)]
                if not candidates and player.current and titles_similar(track.title, player.current.title):
                    candidates = [(0, player.current)]
                if candidates:
                    pos, dup = candidates[0]
                    where = "currently playing" if pos == 0 else f"#{pos} in queue"
                    duplicate_warning = f"\n⚠️ **{dup.title}** is already {where} — might be a duplicate."

            await player.queue.put_wait(track)
            queue_pos = player.queue.count
            msg = f"Queued **{track.title}** — {track.author} : `{_fmt_ms(track.length)}`"
            if player.playing:
                eta_ms = calculate_eta(player, queue_pos - 1)
                msg += f" (#{queue_pos} in queue · in ≈ {_fmt_ms(eta_ms)})"
            msg += duplicate_warning

        if not player.playing:
            await player.play(player.queue.get())

        try:
            await db_sync.sync_queue(get_pool(), player)
        except Exception:
            logger.exception("Failed to sync music queue after play.")
        response = await interaction.followup.send(msg)
        asyncio.create_task(_delete_after(response, 15))

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
        if not player.playing:
            await player.play(player.queue.get())
        try:
            await db_sync.sync_queue(get_pool(), player)
        except Exception:
            logger.exception("Failed to sync music queue after playnext.")
        response = await interaction.followup.send(
            f"**{track.title}** — {track.author} : `{_fmt_ms(track.length)}` will play next."
        )
        asyncio.create_task(_delete_after(response, 15))

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
        asyncio.create_task(_delete_after(await interaction.original_response(), 5))
        await player.stop(force=True)
        # DB state is updated via on_wavelink_track_start when the next track begins

    @app_commands.command(name="stop", description="Stop playback, clear the queue, and disconnect.")
    async def stop(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
            return
        player = self._player(interaction)
        if not player or player.channel != interaction.user.voice.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        await _cancel_progress_task(player)
        await _disable_now_playing(player)
        player.queue.clear()
        await interaction.response.send_message("Stopped and disconnected.")
        try:
            await db_sync.clear_state(get_pool(), player.guild.id)
        except Exception:
            logger.exception("Failed to clear music state after stop.")
        await player.disconnect()

    @app_commands.command(name="list", description="Show the current queue.")
    async def list_queue(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or (not player.current and player.queue.is_empty):
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        view = QueueView(player)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()

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
        try:
            await db_sync.sync_queue(get_pool(), player)
        except Exception:
            logger.exception("Failed to sync music queue after remove.")
        await interaction.response.send_message(f"Removed **{track.title}** from the queue.")
        asyncio.create_task(_delete_after(await interaction.original_response(), 10))

    @app_commands.command(name="autoplay", description="Toggle autoplay (plays similar music when queue ends).")
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
                "Autoplay **enabled** — I'll keep playing music similar to what's on."
            )
        else:
            player.autoplay = wavelink.AutoPlayMode.partial
            await interaction.response.send_message("Autoplay **disabled** — I'll stop when the queue is empty.")
        try:
            await db_sync.sync_state(get_pool(), player)
        except Exception:
            logger.exception("Failed to sync music state after autoplay toggle.")

    @app_commands.command(name="pause", description="Pause or resume playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
            return
        player = self._player(interaction)
        if not player or player.channel != interaction.user.voice.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        if not player.current:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
            return
        await player.pause(not player.paused)
        try:
            await db_sync.sync_state(get_pool(), player)
        except Exception:
            logger.exception("Failed to sync music state after pause toggle.")
        state = "Paused." if player.paused else "Resumed."
        await interaction.response.send_message(state)
        asyncio.create_task(_delete_after(await interaction.original_response(), 5))

    @app_commands.command(name="nowplaying", description="Show what's currently playing.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.current:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
            return
        await _cancel_progress_task(player)
        await _disable_now_playing(player)
        embed = _build_now_playing_embed(player.current, player)
        view = NowPlayingView(player)
        await interaction.response.send_message(embed=embed, view=view)
        player.now_playing_message = await interaction.original_response()
        view.message = player.now_playing_message
        player._progress_task = asyncio.create_task(_progress_loop(player))

    @app_commands.command(name="played", description="Show the last 10 tracks played this session.")
    async def history(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.recent_tracks:
            await interaction.response.send_message("No tracks have been played yet this session.", ephemeral=True)
            return

        tracks = list(player.recent_tracks)
        lines = [f"`{i}.` [{t.title}]({t.uri}) — {t.author} `{_fmt_ms(t.length)}`" for i, t in enumerate(tracks, 1)]
        embed = discord.Embed(
            title="Session History",
            description="\n".join(lines),
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text=f"{len(tracks)} track(s) played this session.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="autoplay_preview", description="Preview the next autoplay suggestions without playing them."
    )
    async def autoplay_preview(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
            return
        if not player.autoplay_enabled:
            await interaction.response.send_message(
                "Autoplay is currently disabled — enable it with `/autoplay` first.", ephemeral=True
            )
            return

        tracks = list(player.auto_queue)[:5]
        if not tracks:
            await interaction.response.send_message(
                "No suggestions ready yet — YouTube is still fetching recommendations.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Autoplay Preview",
            description="Next up from YouTube Music recommendations:\n\n",
            color=discord.Color.blurple(),
        )
        lines = [f"`{i}.` [{t.title}]({t.uri}) — {t.author} `{_fmt_ms(t.length)}`" for i, t in enumerate(tracks, 1)]
        embed.description += "\n".join(lines)
        embed.set_footer(text="These are YouTube's recommendations based on what's currently playing.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="lyrics", description="Show lyrics for the current track.")
    async def lyrics(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.current:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        track = player.current
        params = {"track_name": track.title, "artist_name": track.author or ""}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://lrclib.net/api/get", params=params, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 404:
                        await interaction.followup.send("No lyrics found for this track.", ephemeral=True)
                        return
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception:
            await interaction.followup.send("Could not fetch lyrics — try again later.", ephemeral=True)
            return

        plain = (data.get("plainLyrics") or "").strip()
        if not plain:
            await interaction.followup.send("No lyrics found for this track.", ephemeral=True)
            return

        view = LyricsView(track.title, plain)
        msg = await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = msg

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = cast("MusicPlayer | None", payload.player)
        if not isinstance(player, MusicPlayer):
            return

        track = payload.track
        requester_id = getattr(track.extras, "requester", None)

        if not requester_id and player.autoplay_enabled and is_filtered_autoplay_track(track):
            await player.stop(force=True)
            return

        player.played_ids.add(track.identifier)
        player.recent_tracks.append(track)

        try:
            pool = get_pool()
        except Exception:
            logger.exception("Failed to get DB pool on track start.")
        else:
            try:
                await db_sync.sync_state(pool, player)
            except Exception:
                logger.exception("Failed to sync music state on track start.")
            try:
                await db_sync.sync_queue(pool, player)
            except Exception:
                logger.exception("Failed to sync music queue on track start.")

        if requester_id and player.autoplay_enabled:
            player.auto_queue.reset()

        if not player.text_channel:
            return

        await _cancel_progress_task(player)
        await _disable_now_playing(player)
        embed = _build_now_playing_embed(track, player)
        view = NowPlayingView(player)
        try:
            player.now_playing_message = await player.text_channel.send(embed=embed, view=view)
            view.message = player.now_playing_message
            player._progress_task = asyncio.create_task(_progress_loop(player))
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        if not isinstance(player, MusicPlayer):
            return
        await _cancel_progress_task(player)
        await _disable_now_playing(player)
        if player.text_channel:
            try:
                await player.text_channel.send("Left the voice channel due to inactivity.")
            except discord.HTTPException:
                pass
        try:
            await db_sync.clear_state(get_pool(), player.guild.id)
        except Exception:
            logger.exception("Failed to clear music state on inactive player.")
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
            await _cancel_progress_task(player)
            await _disable_now_playing(player)
            if player.text_channel:
                try:
                    await player.text_channel.send("Everyone left — disconnecting.")
                except discord.HTTPException:
                    pass
            player.queue.clear()
            try:
                await db_sync.clear_state(get_pool(), player.guild.id)
            except Exception:
                logger.exception("Failed to clear music state when everyone left.")
            await player.disconnect()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))

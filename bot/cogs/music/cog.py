from __future__ import annotations

import asyncio
from typing import cast

import discord
import wavelink
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.music import db_sync, panel, service
from bot.cogs.music.embeds import (
    REASON_EVERYONE_LEFT,
    REASON_INACTIVITY,
    build_now_playing_embed,
    stopped_by,
)
from bot.cogs.music.player import MusicPlayer
from bot.cogs.music.utils import (
    NODE_UNAVAILABLE,
    PLAYBACK_FAILED,
    PLAYBACK_STUCK,
    _fmt_ms,
    calculate_eta,
    fetch_lyrics,
    is_filtered_autoplay_track,
    search_failure_message,
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


async def _clear_transient_card(player: MusicPlayer) -> None:
    """Remove the transient Now-Playing card of an *unconfigured* guild.

    Only reachable when `/setup music` has never run: with a pinned card there is nothing
    transient to clean up. It deletes rather than strips the buttons, because stripping
    left one dead embed per track behind — a 30-track playlist littered the channel with 30
    of them, and their buttons stop working after a restart anyway (no custom_id).
    """
    if player.now_playing_message:
        try:
            await player.now_playing_message.delete()
        except discord.HTTPException:
            pass
        player.now_playing_message = None


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]
        # Guilds whose pinned card currently shows a track. The ticker uses it to notice a
        # player that vanished through a path we don't hook — dragged out of the channel by
        # an admin, a node that dropped — and flip the card back to idle. Without it such a
        # card would advertise a song that stopped playing an hour ago.
        self._card_playing: set[int] = set()
        # Why playback ended, consumed by the next idle redraw. Deliberately in memory and
        # not in the DB: it belongs to this session, and a restart is not a reason anyone
        # needs to read about.
        self._idle_reason: dict[int, str] = {}
        self._history_dirty: set[int] = set()
        # Guards against a tick starting while the previous one is still in flight — see
        # `panel_refresh_ticker`.
        self._ticking = False
        self._boot_reconciled = False

    async def cog_unload(self) -> None:
        self.panel_refresh_ticker.cancel()
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
        # Dispatches clicks on the pinned Now-Playing card, which outlives every restart.
        # The view resolves the live player itself, so a card whose player is long gone says
        # "nothing is playing anymore" instead of throwing "This interaction failed".
        self.bot.add_view(NowPlayingView())

        node = wavelink.Node(uri=self.config.lavalink_uri, password=self.config.lavalink_password)
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot)
            logger.info("Connected to Lavalink node at {}.", self.config.lavalink_uri)
        except Exception:
            logger.exception("Could not connect to Lavalink — music commands will be unavailable.")

        # LavaSrc is loaded either way, so a keyless deploy looks identical to a working one
        # from here: every Spotify link just fails at load time, one member at a time. Say it
        # once, at boot, like the betting providers do.
        if self.config.spotify_configured:
            logger.info("Spotify links enabled.")
        else:
            logger.warning("SPOTIFY_CLIENT_ID unset — Spotify links will not resolve.")

        self._cmd_task = asyncio.create_task(self._command_poll_loop())
        self.panel_refresh_ticker.start()

    # ---------------------------------------------------------------- pinned card

    @tasks.loop(seconds=10)
    async def panel_refresh_ticker(self) -> None:
        """One ticker for both pinned cards.

        This replaces the per-player progress task that used to be spawned per track. The
        Now-Playing card is *considered* for redraw every tick while something is playing,
        and the history is drained from a dirty flag, so a burst of skips is one edit rather
        than ten. `panel._edit` drops the redraw when the rendering hasn't actually moved —
        without that, "redraw every tick" meant a guaranteed PATCH every tick, because the
        progress bar makes every payload unique and Discord never no-ops it.

        The interval is 10s rather than 5s for the same reason: the bar is quantised into
        blocks, so 5s bought no visible smoothness and cost twice the requests.
        """
        # `discord.ext.tasks` schedules the next iteration from the previous *scheduled*
        # time, not from when the body returned. A body that overruns its interval therefore
        # re-fires with zero delay and never catches up. Skipping a tick that arrives while
        # the previous one is still running is what stops that from compounding.
        if self._ticking:
            return
        self._ticking = True
        try:
            # Drain first: a track starting while we redraw must dirty the flag again rather
            # than be swallowed by the refresh already in flight.
            dirty, self._history_dirty = self._history_dirty, set()
            for guild_id in dirty:
                await panel.redraw_history(self.bot, guild_id)

            for guild in self.bot.guilds:
                player = cast("MusicPlayer | None", guild.voice_client)
                if isinstance(player, MusicPlayer) and player.current:
                    self._card_playing.add(guild.id)
                    if not await panel.redraw_now_playing(self.bot, guild.id, player):
                        await self._refresh_transient_card(player)
                elif guild.id in self._card_playing:
                    self._card_playing.discard(guild.id)
                    await panel.redraw_now_playing(self.bot, guild.id, None, self._idle_reason.pop(guild.id, None))
        finally:
            self._ticking = False

    @panel_refresh_ticker.before_loop
    async def before_panel_refresh_ticker(self) -> None:
        await self.bot.wait_until_ready()
        await self._boot_reconcile()

    @panel_refresh_ticker.error
    async def panel_refresh_ticker_error(self, error: BaseException) -> None:
        # This does *not* retry on its own: in discord.py a triggered error handler is the end
        # of the loop, so the old "will retry next tick" was wrong and the cards would have
        # silently frozen until the next restart. Restart it explicitly.
        logger.warning(f"panel_refresh_ticker error — restarting the ticker: {error}")
        self._ticking = False
        self.panel_refresh_ticker.restart()

    async def _boot_reconcile(self) -> None:
        """Reset every configured guild's cards after a restart.

        No wavelink player survives a restart, so a `music_state` row saying "playing" is a
        lie — to the pinned card and to whatever reads `music_state` / `music_commands`
        from outside. Runs after `wait_until_ready`, or `get_channel` would miss on a cache
        that isn't populated yet and the redraw would silently do nothing.

        Once per *process*, not once per loop start. `before_loop` runs again whenever the
        ticker is restarted after an error, and clearing `music_state` mid-playback would
        wipe rows the dashboard is reading and reset a live card to idle over a transient
        blip.
        """
        if self._boot_reconciled:
            return
        self._boot_reconciled = True
        try:
            guild_ids = await service.get_configured_guild_ids()
        except Exception:
            logger.exception("Could not load music configs at boot.")
            return
        for guild_id in guild_ids:
            try:
                await db_sync.clear_state(get_pool(), guild_id)
                await panel.redraw_now_playing(self.bot, guild_id, None)
                await panel.redraw_history(self.bot, guild_id)
            except Exception:
                logger.exception("Failed to reset music cards for guild {}.", guild_id)

    async def _refresh_transient_card(self, player: MusicPlayer) -> None:
        """Progress-bar refresh for an unconfigured guild's throwaway card."""
        if not player.now_playing_message or not player.current:
            return
        try:
            await player.now_playing_message.edit(embed=build_now_playing_embed(player.current, player))
        except discord.NotFound:
            player.now_playing_message = None
        except (TimeoutError, discord.HTTPException):
            pass

    async def refresh_cards(self, guild_id: int) -> None:
        """Redraw both pinned cards now. Used by `/setup music` so they're never left 'Loading...'."""
        player = None
        guild = self.bot.get_guild(guild_id)
        if guild and isinstance(guild.voice_client, MusicPlayer):
            player = guild.voice_client
            self._card_playing.add(guild_id)
        await panel.redraw_now_playing(self.bot, guild_id, player)
        await panel.redraw_history(self.bot, guild_id)

    async def _end_playback(self, player: MusicPlayer, reason: str) -> None:
        """Flip the card to idle with a reason, or say it out loud when there's no card.

        The three ways playback ends — inactivity, an empty voice channel, `/stop` — used to
        each post their own message and nothing ever cleaned them up. In a configured guild
        the reason lands in the pinned card's footer and the channel stays silent.
        """
        guild_id = player.guild.id
        self._card_playing.discard(guild_id)
        self._idle_reason.pop(guild_id, None)
        if await panel.redraw_now_playing(self.bot, guild_id, None, reason):
            return
        await _clear_transient_card(player)
        if player.text_channel:
            try:
                await player.text_channel.send(reason)
            except discord.HTTPException:
                pass

    async def _confirm(self, interaction: discord.Interaction, message: str) -> None:
        """Post a queue confirmation, in the music channel when the guild has one.

        These are the one thing members *want* to see land in the channel, so they stay
        public — but they self-delete after 15s, which is what keeps the channel clean.
        """
        channel = await panel.resolve_channel(self.bot, interaction.guild_id)
        if channel is not None and channel.id != interaction.channel_id:
            try:
                posted = await channel.send(message)
            except discord.HTTPException:
                logger.warning("Could not post the queue confirmation in #{}.", channel.name)
            else:
                asyncio.create_task(_delete_after(posted, 15))
                await interaction.followup.send(f"✅ Added — see {channel.mention}.", ephemeral=True)
                return
        response = await interaction.followup.send(message)
        asyncio.create_task(_delete_after(response, 15))

    async def _report_playback_failure(self, player: MusicPlayer, message: str) -> None:
        """Post a playback failure where the member is actually looking, then get out of the way.

        Routed like every other music output: the configured channel first, the channel `/play`
        was typed in as the fallback, nothing at all if there is neither — degraded, not broken.
        Self-deletes after 15s like the queue confirmations, because a channel papered with old
        failures is its own problem.
        """
        channel: discord.abc.Messageable | None = None
        try:
            channel = await panel.resolve_channel(self.bot, player.guild.id)
        except Exception:
            logger.exception("Could not resolve the music channel to report a playback failure.")
        if channel is None:
            channel = player.text_channel
        if channel is None:
            return
        try:
            posted = await channel.send(message)
        except discord.HTTPException:
            return
        asyncio.create_task(_delete_after(posted, 15))

    # ---------------------------------------------------------------- commands

    def _player(self, interaction: discord.Interaction) -> MusicPlayer | None:
        return cast("MusicPlayer | None", interaction.guild.voice_client)

    async def _search_or_report(self, interaction: discord.Interaction, query: str) -> wavelink.Search | None:
        """Search, or tell the member why we couldn't. ``None`` means already reported.

        Note ``None`` is not the same as an empty result: a search that found nothing has its
        own message at the call site, so callers must test ``is None``, not falsiness.

        `LavalinkLoadException` is **not** a subclass of `LavalinkException` — it is what
        `Playable.search` actually raises when Lavalink answers 200 with `loadType: "error"`
        (a dead link, a region block, an unauthenticated LavaSrc). Catching only the latter
        meant every real load failure escaped to `errors.py` and the member got the generic
        apology. The exception's own text never reaches them — it carries provider internals.
        """
        try:
            return await wavelink.Playable.search(query)
        except wavelink.LavalinkLoadException as exc:
            logger.warning(
                "Track load failed for {!r}: {} (severity={}, cause={})", query, exc.error, exc.severity, exc.cause
            )
            message = search_failure_message(query, spotify_configured=self.config.spotify_configured)
        except (wavelink.InvalidNodeException, wavelink.NodeException, wavelink.LavalinkException):
            # cog_load swallows a failed connection, so "no node at all" reaches here.
            logger.exception("Lavalink unavailable while searching {!r}", query)
            message = NODE_UNAVAILABLE
        await interaction.followup.send(message, ephemeral=True)
        return None

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
            # The configured channel wins over wherever /play was typed, so an unconfigured
            # guild keeps the old behaviour and a configured one keeps its music in one place.
            player.text_channel = await panel.resolve_channel(self.bot, interaction.guild_id) or interaction.channel

        tracks = await self._search_or_report(interaction, query)
        if tracks is None:
            # We may have just joined for a query that turned out to be unplayable — don't
            # leave the bot parked in the channel with nothing to say.
            if not player.playing and player.queue.is_empty:
                await player.disconnect()
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
        await self._confirm(interaction, msg)

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

        tracks = await self._search_or_report(interaction, query)
        if tracks is None:
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
        await self._confirm(
            interaction, f"**{track.title}** — {track.author} : `{_fmt_ms(track.length)}` will play next."
        )

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
            return
        player = self._player(interaction)
        if not player or player.channel != interaction.user.voice.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        await interaction.response.send_message("Skipped.", ephemeral=True)
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
        player.queue.clear()
        await interaction.response.send_message("Stopped and disconnected.", ephemeral=True)
        await self._end_playback(player, stopped_by(interaction.user.display_name))
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
        await interaction.response.send_message(f"Removed **{track.title}** from the queue.", ephemeral=True)

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
                "Autoplay **enabled** — I'll keep playing music similar to what's on.", ephemeral=True
            )
        else:
            player.autoplay = wavelink.AutoPlayMode.partial
            await interaction.response.send_message(
                "Autoplay **disabled** — I'll stop when the queue is empty.", ephemeral=True
            )
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
        await interaction.response.send_message(state, ephemeral=True)

    @app_commands.command(name="nowplaying", description="Show what's currently playing.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.current:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
            return

        # With a permanent pinned card, posting a second one would reintroduce exactly the
        # churn it exists to remove — so point at it instead.
        link = await panel.now_playing_link(self.bot, interaction.guild_id)
        if link:
            await interaction.response.send_message(
                f"**{player.current.title}** — {player.current.author}\n{link}", ephemeral=True
            )
            return

        await _clear_transient_card(player)
        embed = build_now_playing_embed(player.current, player)
        view = NowPlayingView(player)
        await interaction.response.send_message(embed=embed, view=view)
        player.now_playing_message = await interaction.original_response()
        view.message = player.now_playing_message

    @app_commands.command(name="lyrics", description="Show lyrics for the current track.")
    async def lyrics(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.current:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        track = player.current
        lyrics = await fetch_lyrics(track.title, track.author or "")
        if not lyrics:
            await interaction.followup.send("No lyrics found for this track.", ephemeral=True)
            return

        view = LyricsView(track.title, lyrics)
        msg = await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = msg

    # ---------------------------------------------------------------- listeners

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
            try:
                await service.record_play(
                    player.guild.id,
                    title=track.title,
                    author=track.author,
                    uri=track.uri,
                    artwork=track.artwork,
                    requester_id=requester_id,
                )
                self._history_dirty.add(player.guild.id)
            except Exception:
                logger.exception("Failed to record play history on track start.")

        if requester_id and player.autoplay_enabled:
            player.auto_queue.reset()

        self._card_playing.add(player.guild.id)
        if await panel.redraw_now_playing(self.bot, player.guild.id, player):
            return

        # No `/setup music` in this guild: keep the old transient card, posted where /play
        # was used. Degraded, not broken.
        if not player.text_channel:
            return
        await _clear_transient_card(player)
        view = NowPlayingView(player)
        try:
            player.now_playing_message = await player.text_channel.send(
                embed=build_now_playing_embed(track, player), view=view
            )
            view.message = player.now_playing_message
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload) -> None:
        """Say out loud that a track Lavalink had accepted then failed to actually play.

        The search path has `_search_or_report`; this is the same rule one step later, and it
        was missing. A track that dies at *format loading* — a YouTube client that broke, a
        dead link, a region block — only ever showed up in the container's logs, so "the bot
        won't play music" was a thing you diagnosed with `docker logs` instead of reading the
        channel.

        **It must not skip.** Lavalink follows `TrackExceptionEvent` with a `TrackEndEvent`,
        and wavelink already schedules `player._auto_play_event` off that one, so the queue
        advances on its own. Calling `skip()` here would eat the *next* track as well.
        """
        player = cast("MusicPlayer | None", payload.player)
        if not isinstance(player, MusicPlayer):
            return
        exception = payload.exception or {}
        logger.warning(
            "Lavalink could not play {!r}: {} (severity={}, cause={})",
            payload.track.title,
            exception.get("message", ""),
            exception.get("severity", "?"),
            exception.get("cause", "?"),
        )
        await self._report_playback_failure(player, PLAYBACK_FAILED.format(title=payload.track.title))

    @commands.Cog.listener()
    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload) -> None:
        """A track that stopped producing audio frames without ever ending.

        Unlike an exception, this is *not* followed by a `TrackEndEvent`, so nothing advances
        the queue — left alone the player sits on a silent track forever. Hence the explicit
        skip, guarded on the stuck track still being the current one: if Lavalink did end it
        after all, `player.current` is already `None` and we must not advance twice.
        """
        player = cast("MusicPlayer | None", payload.player)
        if not isinstance(player, MusicPlayer):
            return
        logger.warning("Track {!r} stuck for {}ms — skipping.", payload.track.title, payload.threshold)
        await self._report_playback_failure(player, PLAYBACK_STUCK.format(title=payload.track.title))
        current = player.current
        if current is not None and current.identifier == payload.track.identifier:
            await player.skip(force=True)

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        if not isinstance(player, MusicPlayer):
            return
        await self._end_playback(player, REASON_INACTIVITY)
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
            await self._end_playback(player, REASON_EVERYONE_LEFT)
            player.queue.clear()
            try:
                await db_sync.clear_state(get_pool(), player.guild.id)
            except Exception:
                logger.exception("Failed to clear music state when everyone left.")
            await player.disconnect()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))

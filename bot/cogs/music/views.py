from __future__ import annotations

import discord
import wavelink

from bot.cogs.music.player import MusicPlayer
from bot.cogs.music.utils import _fmt_ms, fetch_lyrics

_TRACKS_PER_PAGE = 10
_LYRICS_PAGE_SIZE = 1500


class LyricsView(discord.ui.View):
    def __init__(self, title: str, lyrics: str) -> None:
        super().__init__(timeout=300)
        self.title = title
        self.pages = [lyrics[i : i + _LYRICS_PAGE_SIZE] for i in range(0, len(lyrics), _LYRICS_PAGE_SIZE)]
        self.page = 0
        self.message: discord.Message | None = None

        self._prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary)
        self._prev_btn.callback = self._prev

        self._next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
        self._next_btn.callback = self._next

        self.add_item(self._prev_btn)
        self.add_item(self._next_btn)
        self._sync()

    def _sync(self) -> None:
        self._prev_btn.disabled = self.page == 0
        self._next_btn.disabled = self.page >= len(self.pages) - 1

    def build_embed(self) -> discord.Embed:
        page_info = f"Page {self.page + 1}/{len(self.pages)}" if len(self.pages) > 1 else ""
        embed = discord.Embed(
            title=f"Lyrics — {self.title}",
            description=self.pages[self.page],
            color=discord.Color.purple(),
        )
        if page_info:
            embed.set_footer(text=page_info)
        return embed

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(len(self.pages) - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class NowPlayingView(discord.ui.View):
    """The controls under the Now-Playing card.

    Persistent (``timeout=None`` + custom_ids), because this sits on a *public* message.
    It used to time out after 15 minutes, which left buttons that still looked clickable
    but threw "This interaction failed" — and every button died on restart besides.

    The player is therefore resolved from the guild at click time rather than captured:
    a card that outlives its player must be able to say so.

    ``idle=True`` renders every button greyed out, for the resting state of the *pinned*
    card. It is a render-time flag only — the persistent view registered at boot still
    dispatches, so a click that races the redraw is answered rather than dropped.
    """

    def __init__(self, player: MusicPlayer | None = None, *, idle: bool = False) -> None:
        super().__init__(timeout=None)
        self.player = player
        self.message: discord.Message | None = None

        # Row 0 — playback controls
        self._prev_track_btn = discord.ui.Button(
            label="⏮ Prev", style=discord.ButtonStyle.secondary, row=0, custom_id="music:prev"
        )
        self._prev_track_btn.callback = self._play_previous

        self._pause_btn = discord.ui.Button(
            label="⏸ Pause", style=discord.ButtonStyle.secondary, row=0, custom_id="music:pause"
        )
        self._pause_btn.callback = self._toggle_pause

        self._skip_btn = discord.ui.Button(
            label="⏭ Skip", style=discord.ButtonStyle.secondary, row=0, custom_id="music:skip"
        )
        self._skip_btn.callback = self._skip

        self._autoplay_btn = discord.ui.Button(
            label="🔀 Autoplay: Off", style=discord.ButtonStyle.secondary, row=0, custom_id="music:autoplay"
        )
        self._autoplay_btn.callback = self._toggle_autoplay

        # Row 1 — utilities
        self._lyrics_btn = discord.ui.Button(
            label="🎵 Lyrics", style=discord.ButtonStyle.secondary, row=1, custom_id="music:lyrics"
        )
        self._lyrics_btn.callback = self._show_lyrics

        self._queue_btn = discord.ui.Button(
            label="📋 Queue", style=discord.ButtonStyle.secondary, row=1, custom_id="music:queue"
        )
        self._queue_btn.callback = self._show_queue

        self.add_item(self._prev_track_btn)
        self.add_item(self._pause_btn)
        self.add_item(self._skip_btn)
        self.add_item(self._autoplay_btn)
        self.add_item(self._lyrics_btn)
        self.add_item(self._queue_btn)
        if idle:
            for item in self.children:
                item.disabled = True  # type: ignore[attr-defined]
        elif self.player:
            self._sync()

    def _resolve(self, interaction: discord.Interaction) -> MusicPlayer | None:
        """The live player for this guild — the one captured at post time may be long gone."""
        player = self.player or getattr(interaction.guild, "voice_client", None)
        return player if isinstance(player, MusicPlayer) else None

    async def _active_player(self, interaction: discord.Interaction) -> MusicPlayer | None:
        """Resolve the player and check the presser is in the call. Explains itself on failure."""
        player = self._resolve(interaction)
        if player is None:
            await interaction.response.send_message("Nothing is playing anymore.", ephemeral=True)
            return None
        voice = getattr(interaction.user, "voice", None)
        if not voice or voice.channel != player.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return None
        return player

    def _sync(self) -> None:
        if not self.player:
            return
        paused = self.player.paused
        self._pause_btn.label = "▶ Resume" if paused else "⏸ Pause"
        self._pause_btn.style = discord.ButtonStyle.primary if paused else discord.ButtonStyle.secondary

        on = self.player.autoplay_enabled
        self._autoplay_btn.label = f"🔀 Autoplay: {'On' if on else 'Off'}"
        self._autoplay_btn.style = discord.ButtonStyle.green if on else discord.ButtonStyle.secondary

        self._prev_track_btn.disabled = len(self.player.recent_tracks) < 2

    async def _play_previous(self, interaction: discord.Interaction) -> None:
        player = await self._active_player(interaction)
        if not player:
            return
        recent = list(player.recent_tracks)
        if len(recent) < 2:
            await interaction.response.send_message("No previous track available.", ephemeral=True)
            return
        player.queue.put_at(0, recent[-2])
        await interaction.response.defer()
        await player.stop(force=True)

    async def _toggle_pause(self, interaction: discord.Interaction) -> None:
        player = await self._active_player(interaction)
        if not player:
            return
        await player.pause(not player.paused)
        self.player = player
        self._sync()
        await interaction.response.edit_message(view=self)

    async def _skip(self, interaction: discord.Interaction) -> None:
        player = await self._active_player(interaction)
        if not player:
            return
        await interaction.response.defer()
        await player.stop(force=True)

    async def _toggle_autoplay(self, interaction: discord.Interaction) -> None:
        player = await self._active_player(interaction)
        if not player:
            return
        player.autoplay_enabled = not player.autoplay_enabled
        player.autoplay = wavelink.AutoPlayMode.enabled if player.autoplay_enabled else wavelink.AutoPlayMode.partial
        self.player = player
        self._sync()
        await interaction.response.edit_message(view=self)

    async def _show_lyrics(self, interaction: discord.Interaction) -> None:
        player = self._resolve(interaction)
        track = player.current if player else None
        if not track:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        lyrics = await fetch_lyrics(track.title, track.author or "")
        if not lyrics:
            await interaction.followup.send("No lyrics found for this track.", ephemeral=True)
            return
        view = LyricsView(track.title, lyrics)
        msg = await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = msg

    async def _show_queue(self, interaction: discord.Interaction) -> None:
        player = self._resolve(interaction)
        if not player:
            await interaction.response.send_message("Nothing is playing anymore.", ephemeral=True)
            return
        view = QueueView(player)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()


class QueueView(discord.ui.View):
    def __init__(self, player: MusicPlayer) -> None:
        super().__init__(timeout=120)
        self.player = player
        self.page = 0
        self.message: discord.Message | None = None

        self._prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary)
        self._prev_btn.callback = self._prev

        self._refresh_btn = discord.ui.Button(label="🔄", style=discord.ButtonStyle.secondary)
        self._refresh_btn.callback = self._refresh

        self._next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
        self._next_btn.callback = self._next

        self.add_item(self._prev_btn)
        self.add_item(self._refresh_btn)
        self.add_item(self._next_btn)
        self._sync()

    @property
    def _max_page(self) -> int:
        count = self.player.queue.count
        return max(0, (count - 1) // _TRACKS_PER_PAGE) if count > 0 else 0

    def _sync(self) -> None:
        self._prev_btn.disabled = self.page == 0
        self._next_btn.disabled = self.page >= self._max_page

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Queue", color=discord.Color.blurple())
        lines: list[str] = []

        current = self.player.current
        if current and self.page == 0:
            lines.append(
                f"**Now playing:** [{current.title}]({current.uri}) — {current.author} `{_fmt_ms(current.length)}`"
            )

        queue_tracks = list(self.player.queue)
        start = self.page * _TRACKS_PER_PAGE
        for i, track in enumerate(queue_tracks[start : start + _TRACKS_PER_PAGE], start=start + 1):
            requester_id = getattr(track.extras, "requester", None)
            req = f"<@{requester_id}>" if requester_id else "—"
            lines.append(f"`{i}.` [{track.title}]({track.uri}) — {track.author} `{_fmt_ms(track.length)}` · {req}")

        embed.description = "\n".join(lines) if lines else "Queue is empty."

        if current and current.artwork:
            embed.set_thumbnail(url=current.artwork)

        total_ms = (current.length if current else 0) + sum(t.length for t in queue_tracks)
        page_str = f"Page {self.page + 1}/{self._max_page + 1} · " if self._max_page > 0 else ""
        embed.set_footer(text=f"{page_str}{self.player.queue.count} track(s) in queue — Total: {_fmt_ms(total_ms)}")
        return embed

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self.page = min(self.page, self._max_page)
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(self._max_page, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

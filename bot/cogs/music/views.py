from __future__ import annotations

import discord
import wavelink

from bot.cogs.music.player import MusicPlayer

_TRACKS_PER_PAGE = 10


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class NowPlayingView(discord.ui.View):
    def __init__(self, player: MusicPlayer) -> None:
        super().__init__(timeout=None)
        self.player = player

        self._skip_btn = discord.ui.Button(label="⏭ Skip", style=discord.ButtonStyle.secondary)
        self._skip_btn.callback = self._skip

        self._autoplay_btn = discord.ui.Button(label="", style=discord.ButtonStyle.secondary)
        self._autoplay_btn.callback = self._toggle_autoplay

        self.add_item(self._skip_btn)
        self.add_item(self._autoplay_btn)
        self._sync()

    def _sync(self) -> None:
        on = self.player.autoplay_enabled
        self._autoplay_btn.label = f"🔀 Autoplay: {'On' if on else 'Off'}"
        self._autoplay_btn.style = discord.ButtonStyle.green if on else discord.ButtonStyle.secondary

    async def _skip(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or interaction.user.voice.channel != self.player.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.player.stop(force=True)

    async def _toggle_autoplay(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice or interaction.user.voice.channel != self.player.channel:
            await interaction.response.send_message("Join my voice channel first.", ephemeral=True)
            return
        self.player.autoplay_enabled = not self.player.autoplay_enabled
        self.player.autoplay = (
            wavelink.AutoPlayMode.enabled if self.player.autoplay_enabled else wavelink.AutoPlayMode.partial
        )
        self._sync()
        await interaction.response.edit_message(view=self)


class QueueView(discord.ui.View):
    def __init__(self, player: MusicPlayer) -> None:
        super().__init__(timeout=120)
        self.player = player
        self.page = 0
        self.message: discord.Message | None = None

        self._prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary)
        self._prev_btn.callback = self._prev

        self._next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
        self._next_btn.callback = self._next

        self.add_item(self._prev_btn)
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

        if self.player.current and self.page == 0:
            cur = self.player.current
            lines.append(f"**Now playing:** [{cur.title}]({cur.uri}) — {cur.author} `{_fmt_ms(cur.length)}`")

        queue_tracks = list(self.player.queue)
        start = self.page * _TRACKS_PER_PAGE
        for i, track in enumerate(queue_tracks[start : start + _TRACKS_PER_PAGE], start=start + 1):
            requester_id = getattr(track.extras, "requester", None)
            req = f"<@{requester_id}>" if requester_id else "—"
            lines.append(f"`{i}.` [{track.title}]({track.uri}) — {track.author} `{_fmt_ms(track.length)}` · {req}")

        embed.description = "\n".join(lines) if lines else "Queue is empty."

        if self.player.current and self.player.current.artwork:
            embed.set_thumbnail(url=self.player.current.artwork)

        total_ms = sum(t.length for t in queue_tracks)
        page_str = f"Page {self.page + 1}/{self._max_page + 1} · " if self._max_page > 0 else ""
        embed.set_footer(text=f"{page_str}{self.player.queue.count} track(s) in queue — Total: {_fmt_ms(total_ms)}")
        return embed

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
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

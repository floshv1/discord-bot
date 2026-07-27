from __future__ import annotations

from collections import deque

import discord
import wavelink


class MusicPlayer(wavelink.Player):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.text_channel: discord.TextChannel = None  # type: ignore[assignment]
        self.autoplay_enabled: bool = False
        self.autoplay = wavelink.AutoPlayMode.partial
        self.inactive_timeout = 300
        # Only used by a guild that has never run `/setup music`. With a pinned card there
        # is no per-track message to hold on to — the cog's ticker edits the pinned one.
        self.now_playing_message: discord.Message | None = None
        self.played_ids: set[str] = set()
        self.recent_tracks: deque[wavelink.Playable] = deque(maxlen=10)

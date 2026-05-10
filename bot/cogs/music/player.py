from __future__ import annotations

import discord
import wavelink


class MusicPlayer(wavelink.Player):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.text_channel: discord.TextChannel = None  # type: ignore[assignment]
        self.autoplay_enabled: bool = False
        self.autoplay = wavelink.AutoPlayMode.partial
        self.inactive_timeout = 300  # seconds before on_wavelink_inactive_player fires

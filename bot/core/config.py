import os

# Event types kept OUT of the log channel. Empty by default: the log channel mirrors
# every event, which is what this server wants.
#
# Set LOG_MUTED_EVENTS to trim it back if the channel ever gets too loud — the usual
# suspects are `message_sent` (one embed per message in the guild), `slash_command`, and
# the voice mute/deafen toggles (a squad on push-to-talk emits hundreds an hour). Muting
# only silences the *channel*; `_log_to_db` still records everything either way.
DEFAULT_MUTED_LOG_EVENTS: frozenset[str] = frozenset()


class ConfigError(Exception):
    pass


class Config:
    def __init__(self) -> None:
        self.discord_token: str = self._require_str("DISCORD_TOKEN")
        self.database_url: str = self._require_str("DATABASE_URL")
        self.guild_id: int = self._require_int("GUILD_ID")
        self.log_channel_id: int = self._require_int("LOG_CHANNEL_ID")
        self.log_ignored_channel_ids: set[int] = self._optional_int_set("LOG_IGNORED_CHANNEL_IDS")
        self.log_muted_events: set[str] = self._optional_str_set("LOG_MUTED_EVENTS", DEFAULT_MUTED_LOG_EVENTS)
        self.lavalink_uri: str = self._optional_str("LAVALINK_URI", "http://lavalink:2333")
        self.lavalink_password: str = self._optional_str("LAVALINK_PASSWORD", "youshallnotpass")
        # Spotify itself is resolved by Lavalink's LavaSrc plugin, which holds the secret.
        # The bot only needs to know *whether* it was set up, so it can say so at boot and
        # explain a failed Spotify link instead of shrugging at the member.
        self.spotify_configured: bool = bool(self._optional_str_or_none("SPOTIFY_CLIENT_ID"))
        self.football_data_api_key: str | None = self._optional_str_or_none("FOOTBALL_DATA_API_KEY")
        self.pandascore_api_key: str | None = self._optional_str_or_none("PANDASCORE_API_KEY")

    @staticmethod
    def _require_str(key: str) -> str:
        value = os.environ.get(key)
        if not value:
            raise ConfigError(f"Required environment variable '{key}' is missing or empty.")
        return value

    @staticmethod
    def _require_int(key: str) -> int:
        raw = os.environ.get(key)
        if not raw:
            raise ConfigError(f"Required environment variable '{key}' is missing or empty.")
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"Environment variable '{key}' must be an integer, got: '{raw}'")

    @staticmethod
    def _optional_int_set(key: str) -> set[int]:
        raw = os.environ.get(key, "")
        result = set()
        for part in raw.split(","):
            part = part.strip()
            if part:
                try:
                    result.add(int(part))
                except ValueError:
                    raise ConfigError(f"Environment variable '{key}' must be comma-separated integers, got: '{part}'")
        return result

    @staticmethod
    def _optional_str_set(key: str, default: frozenset[str]) -> set[str]:
        """Unset falls back to ``default``; set-but-empty means an explicit empty set."""
        raw = os.environ.get(key)
        if raw is None:
            return set(default)
        return {part.strip().lower() for part in raw.split(",") if part.strip()}

    @staticmethod
    def _optional_int(key: str) -> int | None:
        raw = os.environ.get(key)
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"Environment variable '{key}' must be an integer, got: '{raw}'")

    @staticmethod
    def _optional_str(key: str, default: str) -> str:
        return os.environ.get(key) or default

    @staticmethod
    def _optional_str_or_none(key: str) -> str | None:
        return os.environ.get(key) or None

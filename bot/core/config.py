import os


class ConfigError(Exception):
    pass


class Config:
    def __init__(self) -> None:
        self.discord_token: str = self._require_str("DISCORD_TOKEN")
        self.database_url: str = self._require_str("DATABASE_URL")
        self.guild_id: int = self._require_int("GUILD_ID")
        self.log_channel_id: int = self._require_int("LOG_CHANNEL_ID")

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

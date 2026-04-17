import os


class ConfigError(Exception):
    pass


class Config:
    def __init__(self) -> None:
        self.discord_token: str = self._require_str("DISCORD_TOKEN")
        self.database_url: str = self._require_str("DATABASE_URL")
        self.guild_id: int = self._require_int("GUILD_ID")
        self.log_channel_id: int = self._require_int("LOG_CHANNEL_ID")
        self.log_ignored_channel_ids: set[int] = self._optional_int_set("LOG_IGNORED_CHANNEL_IDS")

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
    def _optional_int(key: str) -> int | None:
        raw = os.environ.get(key)
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"Environment variable '{key}' must be an integer, got: '{raw}'")

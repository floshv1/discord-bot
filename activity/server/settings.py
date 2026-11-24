"""Environment for the Activity container. Deliberately independent of `bot.core.config`
(which demands DISCORD_TOKEN): this process is Internet-facing and must never hold it."""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

MIN_SECRET_LENGTH = 32


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    guild_id: int
    database_url: str
    session_secret: str

    @classmethod
    def from_env(cls) -> Settings | None:
        """None when anything is missing or empty: the server then answers 503 and stays alive."""

        def get(name: str) -> str | None:
            return (os.environ.get(name) or "").strip() or None

        client_id = get("ACTIVITY_CLIENT_ID")
        client_secret = get("ACTIVITY_CLIENT_SECRET")
        guild = get("GUILD_ID")
        database_url = get("ACTIVITY_DATABASE_URL")
        secret = get("ACTIVITY_SESSION_SECRET")
        if secret is not None and len(secret) < MIN_SECRET_LENGTH:
            logger.error("ACTIVITY_SESSION_SECRET is shorter than {} characters: Activity disabled", MIN_SECRET_LENGTH)
            return None
        if not (client_id and client_secret and guild and database_url and secret):
            return None
        try:
            guild_id = int(guild)
        except ValueError:
            return None
        return cls(client_id, client_secret, guild_id, database_url, secret)

"""Entry point: `python -m activity.server`. Importing this module has no side effects."""

from __future__ import annotations

import asyncio
import sys

from aiohttp import web
from loguru import logger

DB_ATTEMPTS = 12
DB_DELAY = 5


def configure_logging(sink=sys.stderr) -> None:
    logger.remove()
    logger.add(sink, level="INFO", backtrace=False, diagnose=False)


async def connect_db(database_url: str) -> bool:
    from bot.db.client import create_pool

    for attempt in range(1, DB_ATTEMPTS + 1):
        try:
            await create_pool(database_url)
            return True
        except Exception as exc:  # noqa: BLE001 - the DSN lives in the message: log the type only
            logger.warning("database not ready ({}/{}): {}", attempt, DB_ATTEMPTS, type(exc).__name__)
            if attempt < DB_ATTEMPTS:
                await asyncio.sleep(DB_DELAY)
    return False


async def build():
    from activity.server.app import create_app
    from activity.server.settings import Settings

    settings = Settings.from_env()
    if settings is None:
        logger.warning("Activity not configured: serving 503 on /health and /api/*")
    elif not await connect_db(settings.database_url):
        logger.error("database unreachable after {} attempts: exiting so the restart policy retries", DB_ATTEMPTS)
        raise SystemExit(1)
    return create_app(settings)


def main() -> None:
    configure_logging()
    web.run_app(build(), host="0.0.0.0", port=8080, print=None, access_log=None)  # noqa: S104 - container-internal


if __name__ == "__main__":
    main()

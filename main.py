import logging
import sys

from loguru import logger

from bot.core.bot import DiscordBot
from bot.core.config import Config, ConfigError


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)


def main() -> None:
    try:
        config = Config()
    except ConfigError as e:
        logger.error(f"Configuration error: {e}")
        raise SystemExit(1)

    bot = DiscordBot(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

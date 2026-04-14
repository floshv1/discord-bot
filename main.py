from loguru import logger

from bot.core.bot import DiscordBot
from bot.core.config import Config, ConfigError


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

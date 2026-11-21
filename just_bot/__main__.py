from __future__ import annotations

import logging

from dotenv import load_dotenv

from .bot import RoundForgeBot
from .config import Settings


def main():
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Discord's verbose HTTP logging is unnecessary in routine operation.
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    bot = RoundForgeBot(settings)
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()

from __future__ import annotations

import logging

from dotenv import load_dotenv

from .core.bot import RoundForgeBot
from .core.config import Settings
from .core.extensions import ExtensionError
from .core.schema import SchemaError


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
    try:
        bot = RoundForgeBot(settings)
    except (ExtensionError, SchemaError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()

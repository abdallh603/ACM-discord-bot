from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


def parse_extensions(raw: str | None) -> tuple[str, ...] | None:
    """ENABLED_EXTENSIONS: empty/unset = bundled defaults, "none" = core only, else a comma list."""
    value = (raw or "").strip()
    if not value:
        return None
    if value.casefold() == "none":
        return ()
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    if "none" in names:
        raise ValueError("ENABLED_EXTENSIONS=none cannot be combined with extension names.")
    return tuple(dict.fromkeys(names))


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    guild_id: int | None
    database_path: Path
    channel_ids: frozenset[int]
    log_level: str
    legacy_guild_id: int | None = None
    # None selects every bundled extension that is enabled by default; () is core only.
    extensions: tuple[str, ...] | None = None
    # Raw configuration for extensions' own settings; empty unless loaded from the environment.
    env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError("Set DISCORD_TOKEN in .env before starting the bot.")
        try:
            guild_id = int(os.getenv("DISCORD_GUILD_ID", "").strip() or "0")
            legacy_guild_id = int(os.getenv("LEGACY_GUILD_ID", "").strip() or "0")
            channels = frozenset(
                int(value.strip())
                for value in os.getenv("BOT_CHANNEL_IDS", "").split(",")
                if value.strip()
            )
        except ValueError as exc:
            raise ValueError("Server and channel IDs must be positive integers.") from exc
        if guild_id < 0 or legacy_guild_id < 0 or any(value <= 0 for value in channels):
            raise ValueError("Server/channel IDs must be positive; leave DISCORD_GUILD_ID empty for public mode.")
        if channels and not guild_id:
            raise ValueError("Use /admin channel for public servers, or set DISCORD_GUILD_ID with BOT_CHANNEL_IDS.")
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
        return cls(token, guild_id or None, Path(os.getenv("DATABASE_PATH", "data/roundforge.sqlite3")),
                   channels, level, legacy_guild_id or None,
                   parse_extensions(os.getenv("ENABLED_EXTENSIONS")), dict(os.environ))

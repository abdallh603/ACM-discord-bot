from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    guild_id: int | None
    database_path: Path
    channel_ids: frozenset[int]
    log_level: str
    legacy_guild_id: int | None = None
    activity_enabled: bool = False
    activity_host: str = "127.0.0.1"
    activity_port: int = 8080
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)

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
        enabled = os.getenv("ACTIVITY_ENABLED", "false").strip().lower()
        if enabled not in {"true", "false", "1", "0"}:
            raise ValueError("ACTIVITY_ENABLED must be true or false.")
        enabled = enabled in {"true", "1"}
        try:
            port = int(os.getenv("ACTIVITY_PORT", "8080"))
        except ValueError as exc:
            raise ValueError("ACTIVITY_PORT must be 1–65535.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("ACTIVITY_PORT must be 1–65535.")
        client_id = os.getenv("DISCORD_CLIENT_ID", "").strip() or None
        secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip() or None
        if client_id and (not client_id.isascii() or not client_id.isdigit()):
            raise ValueError("DISCORD_CLIENT_ID must be the application's numeric ID.")
        if enabled and (not client_id or not secret):
            raise ValueError("Set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET locally to enable the voice activity.")
        return cls(token, guild_id or None, Path(os.getenv("DATABASE_PATH", "data/roundforge.sqlite3")),
                   channels, level, legacy_guild_id or None, enabled,
                   os.getenv("ACTIVITY_HOST", "127.0.0.1").strip() or "127.0.0.1", port, client_id, secret)

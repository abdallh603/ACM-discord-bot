"""Activity settings, read from the environment by the extension (not by core)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActivitySettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    # The bot token, used server-side to verify Activity instances; never sent to the browser.
    token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, token: str = "") -> ActivitySettings:
        enabled = env.get("ACTIVITY_ENABLED", "false").strip().lower()
        if enabled not in {"true", "false", "1", "0"}:
            raise ValueError("ACTIVITY_ENABLED must be true or false.")
        enabled = enabled in {"true", "1"}
        try:
            port = int(env.get("ACTIVITY_PORT", "8080"))
        except ValueError as exc:
            raise ValueError("ACTIVITY_PORT must be 1–65535.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("ACTIVITY_PORT must be 1–65535.")
        client_id = env.get("DISCORD_CLIENT_ID", "").strip() or None
        secret = env.get("DISCORD_CLIENT_SECRET", "").strip() or None
        if client_id and (not client_id.isascii() or not client_id.isdigit()):
            raise ValueError("DISCORD_CLIENT_ID must be the application's numeric ID.")
        if enabled and (not client_id or not secret):
            raise ValueError("Set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET locally to enable the voice activity.")
        return cls(enabled, env.get("ACTIVITY_HOST", "127.0.0.1").strip() or "127.0.0.1", port,
                   client_id, secret, token)

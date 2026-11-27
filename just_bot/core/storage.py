from __future__ import annotations

import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from ..tournament_store import TOURNAMENT_SCHEMA
from . import schema
from .domain import UserError

CORE_MIGRATIONS = [
    """
CREATE TABLE core_members (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    handle TEXT NOT NULL COLLATE NOCASE, rating INTEGER, max_rating INTEGER,
    verified_at INTEGER NOT NULL, refreshed_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id), UNIQUE (guild_id, handle)
);
CREATE TABLE core_pending_links (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    handle TEXT NOT NULL, nonce TEXT NOT NULL, expires_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE core_guild_settings (
    guild_id INTEGER PRIMARY KEY, timezone TEXT NOT NULL DEFAULT 'UTC'
);
CREATE TABLE core_guild_channels (
    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, PRIMARY KEY(guild_id, channel_id)
);
""",
]

class Store:
    """Small, local SQLite store. Access only from the bot's event-loop thread."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            tables = schema.objects(self.db)
            if "members" in tables and schema.REGISTRY not in tables:
                raise schema.SchemaError(
                    "This database was created by an older RoundForge build (before extensions) and is not "
                    "migrated. Point DATABASE_PATH at a new file, or delete the old database."
                )
            schema.migrate(self.db, "core", CORE_MIGRATIONS)
            self.db.executescript(TOURNAMENT_SCHEMA)  # TRANSITIONAL until tournaments move
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def member(self, guild: int, user: int):
        return self.db.execute(
            "SELECT * FROM core_members WHERE guild_id=? AND user_id=?", (guild, user)
        ).fetchone()

    def members(self, guild: int):
        return self.db.execute(
            "SELECT * FROM core_members WHERE guild_id=? ORDER BY user_id", (guild,)
        ).fetchall()

    def start_link(self, guild: int, user: int, handle: str, nonce: str, expires: int):
        if self.member(guild, user):
            raise UserError("You already have a linked handle. Use /handle unlink first.")
        if self.db.execute(
            "SELECT 1 FROM core_members WHERE guild_id=? AND handle=?", (guild, handle)
        ).fetchone():
            raise UserError("This handle is already linked to another member in this server.")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO core_pending_links VALUES (?, ?, ?, ?, ?)",
                (guild, user, handle, nonce, expires),
            )

    def pending(self, guild: int, user: int):
        return self.db.execute(
            "SELECT * FROM core_pending_links WHERE guild_id=? AND user_id=?", (guild, user)
        ).fetchone()

    def complete_link(self, guild: int, user: int, nonce: str, profile: dict, now: int):
        try:
            with self.db:
                pending = self.pending(guild, user)
                if not pending or pending["nonce"] != nonce or pending["expires_at"] <= now:
                    raise UserError("Verification expired or was replaced. Run /handle link again.")
                if profile["handle"].casefold() != pending["handle"].casefold():
                    raise UserError("Codeforces returned a different handle. Start linking again.")
                self.db.execute(
                    "INSERT INTO core_members VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (guild, user, profile["handle"], profile.get("rating"),
                     profile.get("maxRating"), now, now),
                )
                self.db.execute(
                    "DELETE FROM core_pending_links WHERE guild_id=? AND user_id=?", (guild, user)
                )
        except sqlite3.IntegrityError as exc:
            raise UserError("You or this handle are already linked in this server.") from exc

    def unlink(self, guild: int, user: int):
        with self.db:
            self.db.execute("DELETE FROM core_members WHERE guild_id=? AND user_id=?", (guild, user))
            self.db.execute(
                "DELETE FROM core_pending_links WHERE guild_id=? AND user_id=?", (guild, user)
            )

    def update_profile(self, guild: int, profile: dict, now: int):
        with self.db:
            self.db.execute(
                "UPDATE core_members SET rating=?, max_rating=?, refreshed_at=? "
                "WHERE guild_id=? AND handle=?",
                (profile.get("rating"), profile.get("maxRating"), now, guild, profile["handle"]),
            )

    def settings(self, guild: int):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO core_guild_settings(guild_id,timezone) VALUES (?, 'UTC')", (guild,))
        return self.db.execute(
            "SELECT * FROM core_guild_settings WHERE guild_id=?", (guild,)
        ).fetchone()

    def timezone(self, guild):
        return ZoneInfo(self.settings(guild)["timezone"])

    def set_timezone(self, guild, timezone):
        ZoneInfo(timezone)
        self.settings(guild)
        with self.db:
            self.db.execute("UPDATE core_guild_settings SET timezone=? WHERE guild_id=?", (timezone, guild))

    def allowed_channels(self, guild):
        return frozenset(row[0] for row in self.db.execute(
            "SELECT channel_id FROM core_guild_channels WHERE guild_id=?", (guild,)
        ))

    def channel_rule(self, guild, action, channel=None):
        with self.db:
            if action == "clear":
                self.db.execute("DELETE FROM core_guild_channels WHERE guild_id=?", (guild,))
            elif action == "add" and channel:
                self.db.execute("INSERT OR IGNORE INTO core_guild_channels VALUES (?, ?)", (guild, channel))
            elif action == "remove" and channel:
                self.db.execute("DELETE FROM core_guild_channels WHERE guild_id=? AND channel_id=?", (guild, channel))
            else:
                raise UserError("Select a channel to add/remove, or use clear.")

    def leaderboard(self, guild: int, page: int):
        offset = (page - 1) * 15
        return self.db.execute(
            "SELECT * FROM core_members WHERE guild_id=? ORDER BY rating DESC, handle COLLATE NOCASE "
            "LIMIT 15 OFFSET ?", (guild, offset),
        ).fetchall()

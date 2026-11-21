from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from .activities_store import ACTIVITY_SCHEMA
from .domain import UserError

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    handle TEXT NOT NULL COLLATE NOCASE, rating INTEGER, max_rating INTEGER,
    verified_at INTEGER NOT NULL, refreshed_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id), UNIQUE (guild_id, handle)
);
CREATE TABLE IF NOT EXISTS pending_links (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    handle TEXT NOT NULL, nonce TEXT NOT NULL, expires_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY, reminders_channel_id INTEGER,
    daily_channel_id INTEGER, daily_rating INTEGER NOT NULL DEFAULT 1200,
    daily_hour INTEGER NOT NULL DEFAULT 9, timezone TEXT NOT NULL DEFAULT 'UTC'
);
CREATE TABLE IF NOT EXISTS guild_channels (
    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, PRIMARY KEY(guild_id, channel_id)
);
CREATE TABLE IF NOT EXISTS rank_roles (
    guild_id INTEGER NOT NULL, rank TEXT NOT NULL, role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, rank), UNIQUE (guild_id, role_id)
);
CREATE TABLE IF NOT EXISTS daily_problems (
    guild_id INTEGER NOT NULL, day TEXT NOT NULL, problem TEXT NOT NULL,
    starts_at INTEGER NOT NULL, ends_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, day)
);
CREATE TABLE IF NOT EXISTS daily_claims (
    guild_id INTEGER NOT NULL, day TEXT NOT NULL, user_id INTEGER NOT NULL,
    handle TEXT NOT NULL COLLATE NOCASE, submission_id INTEGER NOT NULL,
    points INTEGER NOT NULL, claimed_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, day, user_id), UNIQUE (guild_id, day, handle),
    UNIQUE (guild_id, submission_id)
);
CREATE TABLE IF NOT EXISTS deliveries (
    guild_id INTEGER NOT NULL, event_key TEXT NOT NULL, sent_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, event_key)
);
CREATE INDEX IF NOT EXISTS claims_month ON daily_claims(guild_id, day, user_id);
PRAGMA user_version = 4;
"""


class Store:
    """Small, local SQLite store. Access only from the bot's event-loop thread."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            self.db.close()
            raise RuntimeError("Database is newer than this bot. Upgrade the bot before starting.")
        self.db.executescript(SCHEMA)
        self.db.executescript(ACTIVITY_SCHEMA)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(blitz_players)")}
        if "owns_deaf" not in columns:
            with self.db:
                self.db.execute("ALTER TABLE blitz_players ADD COLUMN owns_deaf INTEGER NOT NULL DEFAULT 0")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(guild_settings)")}
        if "timezone" not in columns:
            with self.db:
                # Preserve the old Jordan calendar for existing servers.
                self.db.execute("ALTER TABLE guild_settings ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Asia/Amman'")

    def close(self):
        self.db.close()

    def member(self, guild: int, user: int):
        return self.db.execute(
            "SELECT * FROM members WHERE guild_id=? AND user_id=?", (guild, user)
        ).fetchone()

    def members(self, guild: int):
        return self.db.execute(
            "SELECT * FROM members WHERE guild_id=? ORDER BY user_id", (guild,)
        ).fetchall()

    def start_link(self, guild: int, user: int, handle: str, nonce: str, expires: int):
        if self.member(guild, user):
            raise UserError("You already have a linked handle. Use /handle unlink first.")
        if self.db.execute(
            "SELECT 1 FROM members WHERE guild_id=? AND handle=?", (guild, handle)
        ).fetchone():
            raise UserError("This handle is already linked to another member in this server.")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO pending_links VALUES (?, ?, ?, ?, ?)",
                (guild, user, handle, nonce, expires),
            )

    def pending(self, guild: int, user: int):
        return self.db.execute(
            "SELECT * FROM pending_links WHERE guild_id=? AND user_id=?", (guild, user)
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
                    "INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (guild, user, profile["handle"], profile.get("rating"),
                     profile.get("maxRating"), now, now),
                )
                self.db.execute(
                    "DELETE FROM pending_links WHERE guild_id=? AND user_id=?", (guild, user)
                )
        except sqlite3.IntegrityError as exc:
            raise UserError("You or this handle are already linked in this server.") from exc

    def unlink(self, guild: int, user: int):
        with self.db:
            self.db.execute("DELETE FROM members WHERE guild_id=? AND user_id=?", (guild, user))
            self.db.execute(
                "DELETE FROM pending_links WHERE guild_id=? AND user_id=?", (guild, user)
            )

    def update_profile(self, guild: int, profile: dict, now: int):
        with self.db:
            self.db.execute(
                "UPDATE members SET rating=?, max_rating=?, refreshed_at=? "
                "WHERE guild_id=? AND handle=?",
                (profile.get("rating"), profile.get("maxRating"), now, guild, profile["handle"]),
            )

    def settings(self, guild: int):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO guild_settings(guild_id,timezone) VALUES (?, 'UTC')", (guild,))
        return self.db.execute(
            "SELECT * FROM guild_settings WHERE guild_id=?", (guild,)
        ).fetchone()

    def configure(self, guild: int, reminders: int | None, daily: int | None,
                  rating: int, hour: int):
        self.settings(guild)
        with self.db:
            self.db.execute(
                "UPDATE guild_settings SET reminders_channel_id=?,daily_channel_id=?,daily_rating=?,daily_hour=? "
                "WHERE guild_id=?", (reminders, daily, rating, hour, guild),
            )

    def timezone(self, guild):
        return ZoneInfo(self.settings(guild)["timezone"])

    def set_timezone(self, guild, timezone):
        ZoneInfo(timezone)
        self.settings(guild)
        with self.db:
            self.db.execute("UPDATE guild_settings SET timezone=? WHERE guild_id=?", (timezone, guild))

    def allowed_channels(self, guild):
        return frozenset(row[0] for row in self.db.execute(
            "SELECT channel_id FROM guild_channels WHERE guild_id=?", (guild,)
        ))

    def channel_rule(self, guild, action, channel=None):
        with self.db:
            if action == "clear":
                self.db.execute("DELETE FROM guild_channels WHERE guild_id=?", (guild,))
            elif action == "add" and channel:
                self.db.execute("INSERT OR IGNORE INTO guild_channels VALUES (?, ?)", (guild, channel))
            elif action == "remove" and channel:
                self.db.execute("DELETE FROM guild_channels WHERE guild_id=? AND channel_id=?", (guild, channel))
            else:
                raise UserError("Select a channel to add/remove, or use clear.")

    def roles(self, guild: int) -> dict[str, int]:
        return {r["rank"]: r["role_id"] for r in self.db.execute(
            "SELECT rank, role_id FROM rank_roles WHERE guild_id=?", (guild,)
        )}

    def save_role(self, guild: int, rank: str, role_id: int):
        with self.db:
            self.db.execute(
                "INSERT INTO rank_roles VALUES (?, ?, ?) ON CONFLICT(guild_id, rank) "
                "DO UPDATE SET role_id=excluded.role_id", (guild, rank, role_id),
            )

    def daily(self, guild: int, day: str):
        row = self.db.execute(
            "SELECT * FROM daily_problems WHERE guild_id=? AND day=?", (guild, day)
        ).fetchone()
        return {**dict(row), "problem": json.loads(row["problem"])} if row else None

    def recent_daily_keys(self, guild: int, since: str) -> set[tuple]:
        rows = self.db.execute(
            "SELECT problem FROM daily_problems WHERE guild_id=? AND day>=?", (guild, since)
        )
        return {(p["contestId"], p["index"]) for row in rows for p in [json.loads(row[0])]}

    def save_daily(self, guild: int, day: str, problem: dict, start: int, end: int):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO daily_problems VALUES (?, ?, ?, ?, ?)",
                (guild, day, json.dumps(problem), start, end),
            )
        return self.daily(guild, day)

    def claim(self, guild: int, day: str, user: int, handle: str, submission: int, now: int):
        with self.db:
            # Recheck the link and deadline after the asynchronous Codeforces lookup.
            member = self.member(guild, user)
            daily = self.daily(guild, day)
            if not member or member["handle"].casefold() != handle.casefold():
                raise UserError("Your linked account changed. Please try again.")
            if not daily or not daily["starts_at"] <= now < daily["ends_at"]:
                raise UserError("This daily challenge has ended. Try today's challenge.")
            try:
                self.db.execute(
                    "INSERT INTO daily_claims VALUES (?, ?, ?, ?, ?, 10, ?)",
                    (guild, day, user, handle, submission, now),
                )
            except sqlite3.IntegrityError as exc:
                raise UserError("This member, handle, or submission has already claimed this daily.") from exc

    def leaderboard(self, guild: int, page: int, month: str | None = None):
        offset = (page - 1) * 15
        if month:
            return self.db.execute(
                "SELECT m.*, COALESCE(SUM(c.points), 0) AS points FROM members m "
                "LEFT JOIN daily_claims c ON c.guild_id=m.guild_id AND c.user_id=m.user_id "
                "AND c.day>=? AND c.day<? WHERE m.guild_id=? "
                "GROUP BY m.guild_id, m.user_id ORDER BY points DESC, m.handle COLLATE NOCASE "
                "LIMIT 15 OFFSET ?", (month + "-01", month + "-32", guild, offset),
            ).fetchall()
        return self.db.execute(
            "SELECT * FROM members WHERE guild_id=? ORDER BY rating DESC, handle COLLATE NOCASE "
            "LIMIT 15 OFFSET ?", (guild, offset),
        ).fetchall()

    def was_sent(self, guild: int, key: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM deliveries WHERE guild_id=? AND event_key=?", (guild, key)
        ).fetchone() is not None

    def mark_sent(self, guild: int, key: str):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?, ?, ?)", (guild, key, int(time.time()))
            )

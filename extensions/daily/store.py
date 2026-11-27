"""Daily problems, claims, feed settings, and delivery records for one server at a time."""
from __future__ import annotations

import json
import sqlite3
import time

from just_bot.core.api import UserError

# Append-only: entry i upgrades schema version i to i + 1.
MIGRATIONS = [
    """
CREATE TABLE daily_settings (
    guild_id INTEGER PRIMARY KEY, reminders_channel_id INTEGER,
    daily_channel_id INTEGER, daily_rating INTEGER NOT NULL DEFAULT 1200,
    daily_hour INTEGER NOT NULL DEFAULT 9
);
CREATE TABLE daily_problems (
    guild_id INTEGER NOT NULL, day TEXT NOT NULL, problem TEXT NOT NULL,
    starts_at INTEGER NOT NULL, ends_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, day)
);
CREATE TABLE daily_claims (
    guild_id INTEGER NOT NULL, day TEXT NOT NULL, user_id INTEGER NOT NULL,
    handle TEXT NOT NULL COLLATE NOCASE, submission_id INTEGER NOT NULL,
    points INTEGER NOT NULL, claimed_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, day, user_id), UNIQUE (guild_id, day, handle),
    UNIQUE (guild_id, submission_id)
);
CREATE INDEX daily_claims_month ON daily_claims(guild_id, day, user_id);
CREATE TABLE daily_deliveries (
    guild_id INTEGER NOT NULL, event_key TEXT NOT NULL, sent_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, event_key)
);
""",
]


class DailyStore:
    """Reads core members and timezone through the core store; owns only daily_* tables."""

    def __init__(self, core):
        self.core = core
        self.db = core.db

    def settings(self, guild: int):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO daily_settings(guild_id) VALUES (?)", (guild,))
        return self.db.execute("SELECT * FROM daily_settings WHERE guild_id=?", (guild,)).fetchone()

    def configure(self, guild: int, reminders: int | None, daily: int | None, rating: int, hour: int):
        self.settings(guild)
        with self.db:
            self.db.execute(
                "UPDATE daily_settings SET reminders_channel_id=?,daily_channel_id=?,daily_rating=?,daily_hour=? "
                "WHERE guild_id=?", (reminders, daily, rating, hour, guild),
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
            member = self.core.member(guild, user)
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

    def leaderboard(self, guild: int, page: int, month: str):
        offset = (page - 1) * 15
        return self.db.execute(
            "SELECT m.*, COALESCE(SUM(c.points), 0) AS points FROM core_members m "
            "LEFT JOIN daily_claims c ON c.guild_id=m.guild_id AND c.user_id=m.user_id "
            "AND c.day>=? AND c.day<? WHERE m.guild_id=? "
            "GROUP BY m.guild_id, m.user_id ORDER BY points DESC, m.handle COLLATE NOCASE "
            "LIMIT 15 OFFSET ?", (month + "-01", month + "-32", guild, offset),
        ).fetchall()

    def was_sent(self, guild: int, key: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM daily_deliveries WHERE guild_id=? AND event_key=?", (guild, key)
        ).fetchone() is not None

    def mark_sent(self, guild: int, key: str):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO daily_deliveries VALUES (?, ?, ?)", (guild, key, int(time.time()))
            )

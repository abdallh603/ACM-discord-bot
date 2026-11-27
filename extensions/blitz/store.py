"""Durable blitz games and voice-recovery records, sharing the main SQLite connection."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

from just_bot.core.api import UserError

# Append-only: entry i upgrades schema version i to i + 1.
MIGRATIONS = [
    """
CREATE TABLE blitz_matches (
    id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, creator_id INTEGER NOT NULL,
    state TEXT NOT NULL, options TEXT NOT NULL,
    problem TEXT, created_at INTEGER NOT NULL, invite_expires INTEGER NOT NULL,
    starts_at INTEGER, ends_at INTEGER, winner_id INTEGER, outcome TEXT,
    closed_at INTEGER, text_id INTEGER, voice_id INTEGER, announced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE blitz_players (
    match_id TEXT NOT NULL, user_id INTEGER NOT NULL, handle TEXT NOT NULL,
    original_voice INTEGER, owns_mute INTEGER NOT NULL DEFAULT 0, solved_at INTEGER,
    owns_deaf INTEGER NOT NULL DEFAULT 0,
    accepted_revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(match_id, user_id)
);
CREATE TABLE blitz_slots (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, handle TEXT NOT NULL COLLATE NOCASE,
    match_id TEXT NOT NULL, PRIMARY KEY(guild_id, user_id), UNIQUE(guild_id, handle)
);
CREATE INDEX blitz_guild_state ON blitz_matches(guild_id, state);
""",
]


class BlitzStore:
    def __init__(self, db):
        self.db = db
        # Callables (guild, user, handle, options) that raise UserError to refuse a player;
        # they run inside create_match's transaction (e.g. tournament reservations).
        self.reservation_checks = []

    def slot(self, guild, user):
        return self.db.execute("SELECT * FROM blitz_slots WHERE guild_id=? AND user_id=?",
                               (guild, user)).fetchone()

    def reserved(self, guild, user, handle):
        """Whether this member or handle has a pending/running game or voice recovery."""
        return self.db.execute("SELECT 1 FROM blitz_slots WHERE guild_id=? AND (user_id=? OR handle=?)",
                               (guild, user, handle)).fetchone() is not None

    def with_option(self, guild, key, value):
        """Matches whose options contain ``key: value`` (e.g. games launched by a tournament)."""
        rows = self.db.execute("SELECT id FROM blitz_matches WHERE guild_id=? AND options LIKE ?",
                               (guild, f'%"{key}": {json.dumps(value)}%')).fetchall()
        matches = [self.match(guild, row[0]) for row in rows]
        return [m for m in matches if m["options"].get(key) == value]

    def restorable(self):
        """(guild_id, match_id) for games whose buttons must be re-registered after a restart."""
        return self.db.execute(
            "SELECT guild_id,id FROM blitz_matches WHERE state IN ('pending','active','settling')"
        ).fetchall()

    def create_match(self, guild, creator, options, players, now=None):
        now = int(time.time()) if now is None else now
        valid_size = len(players) == 1 if options.get("solo") else 2 <= len(players) <= 8
        if (not valid_size or len({p[0] for p in players}) != len(players)
                or (creator not in {p[0] for p in players} and not options.get("hosted"))):
            raise UserError("Choose 2–8 distinct players, or one player for a solo test, including the challenger.")
        match_id = uuid.uuid4().hex[:12]
        try:
            with self.db:
                for user, handle in players:
                    for check in self.reservation_checks:
                        check(guild, user, handle, options)
                self.db.execute(
                    "INSERT INTO blitz_matches(id,guild_id,creator_id,state,options,"
                    "created_at,invite_expires) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                    (match_id, guild, creator, json.dumps(options), now, now + 600),
                )
                for user, handle in players:
                    self.db.execute("INSERT INTO blitz_slots VALUES (?, ?, ?, ?)",
                                    (guild, user, handle, match_id))
                    self.db.execute("INSERT INTO blitz_players(match_id,user_id,handle,accepted_revision) VALUES (?, ?, ?, ?)",
                                    (match_id, user, handle, options["revision"] if user == creator
                                     and not options.get("hosted") else 0))
        except sqlite3.IntegrityError as exc:
            raise UserError("One of these members already has a blitz invite, match, or pending mute recovery.") from exc
        return self.match(guild, match_id)

    def match(self, guild, match_id):
        row = self.db.execute("SELECT * FROM blitz_matches WHERE guild_id=? AND id=?",
                              (guild, match_id)).fetchone()
        if not row:
            raise UserError("Blitz match not found in this server.")
        result = dict(row)
        result["options"] = json.loads(result["options"])
        result["problem"] = json.loads(result["problem"]) if result["problem"] else None
        return result

    def matches(self, guild):
        return [self.match(guild, r[0]) for r in self.db.execute(
            "SELECT id FROM blitz_matches WHERE guild_id=? AND "
            "(state IN ('pending','starting','countdown','active','settling') OR announced=0 OR voice_id IS NOT NULL "
            "OR text_id IS NOT NULL OR id IN (SELECT match_id FROM blitz_players WHERE owns_mute=1 OR owns_deaf=1))",
            (guild,),
        )]

    def players(self, match_id):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM blitz_players WHERE match_id=? ORDER BY user_id", (match_id,)
        )]

    def in_channel(self, guild, channel):
        rows = self.db.execute(
            "SELECT id FROM blitz_matches WHERE guild_id=? AND "
            "(text_id=? OR state IN ('pending','starting','countdown'))", (guild, channel)
        ).fetchall()
        matches = [self.match(guild, row[0]) for row in rows]
        return [m for m in matches if m["text_id"] == channel
                or (m["state"] in {"pending", "starting", "countdown"} and m["options"].get("text_channel_id") == channel)]

    def update_match(self, match_id, **changes):
        allowed = {"state", "options", "problem", "starts_at", "ends_at", "winner_id", "outcome",
                   "closed_at", "text_id", "voice_id", "announced"}
        if not changes.keys() <= allowed:
            raise ValueError("Invalid blitz field")
        values = [json.dumps(v) if k in {"options", "problem"} and v is not None else v
                  for k, v in changes.items()]
        with self.db:
            self.db.execute("UPDATE blitz_matches SET " + ", ".join(f"{k}=?" for k in changes) + " WHERE id=?",
                            (*values, match_id))

    def update_player(self, match_id, user, **changes):
        if not changes.keys() <= {"original_voice", "owns_mute", "owns_deaf", "solved_at", "accepted_revision"}:
            raise ValueError("Invalid player field")
        with self.db:
            self.db.execute("UPDATE blitz_players SET " + ", ".join(f"{k}=?" for k in changes)
                            + " WHERE match_id=? AND user_id=?", (*changes.values(), match_id, user))

    def free_slots(self, match_id):
        # Keep a participant reserved until their bot-owned mute is cleared.
        with self.db:
            self.db.execute(
                "DELETE FROM blitz_slots WHERE match_id=? AND user_id NOT IN "
                "(SELECT user_id FROM blitz_players WHERE match_id=? AND (owns_mute=1 OR owns_deaf=1 OR original_voice IS NOT NULL))",
                (match_id, match_id),
            )

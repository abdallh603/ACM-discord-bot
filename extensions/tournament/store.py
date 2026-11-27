"""Durable single-elimination brackets, sharing the main SQLite connection."""
from __future__ import annotations

import json

from just_bot.core.api import UserError

# Append-only: entry i upgrades schema version i to i + 1.
MIGRATIONS = [
    """
CREATE TABLE tournament_events (
    id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, creator_id INTEGER NOT NULL,
    name TEXT NOT NULL, state TEXT NOT NULL, options TEXT NOT NULL,
    capacity INTEGER NOT NULL, current_round INTEGER NOT NULL DEFAULT 0,
    champion_id INTEGER, channel_id INTEGER NOT NULL, message_id INTEGER,
    created_at INTEGER NOT NULL, needs_update INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX tournament_events_guild_state ON tournament_events(guild_id,state);
CREATE TABLE tournament_entries (
    tournament_id TEXT NOT NULL, user_id INTEGER NOT NULL, handle TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY(tournament_id,user_id), UNIQUE(tournament_id,handle)
);
CREATE TABLE tournament_slots (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, handle TEXT NOT NULL COLLATE NOCASE,
    tournament_id TEXT NOT NULL, PRIMARY KEY(guild_id,user_id), UNIQUE(guild_id,handle)
);
CREATE TABLE tournament_pairs (
    tournament_id TEXT NOT NULL, round_no INTEGER NOT NULL, position INTEGER NOT NULL,
    player_a INTEGER, player_b INTEGER, winner_id INTEGER, state TEXT NOT NULL,
    blitz_id TEXT, attempt INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(tournament_id,round_no,position)
);
""",
]


class TournamentStore:
    def __init__(self, db):
        self.db = db

    def get(self, guild, tournament_id):
        row = self.db.execute("SELECT * FROM tournament_events WHERE guild_id=? AND id=?",
                              (guild, tournament_id)).fetchone()
        if row is None:
            raise UserError("Tournament not found in this server.")
        result = dict(row)
        result["options"] = json.loads(result["options"])
        return result

    def active(self, guild):
        return [self.get(guild, r[0]) for r in self.db.execute(
            "SELECT id FROM tournament_events WHERE guild_id=? AND state IN ('registration','running','cancelling') ORDER BY created_at,id",
            (guild,),
        ).fetchall()]

    def recent(self, guild, limit=100):
        """The server's newest tournaments in any state (e.g. for the Activity's selector)."""
        return [self.get(guild, r[0]) for r in self.db.execute(
            "SELECT id FROM tournament_events WHERE guild_id=? ORDER BY created_at DESC LIMIT ?", (guild, limit),
        ).fetchall()]

    def restorable(self):
        """Open tournaments with a posted panel whose buttons must be re-registered after a restart."""
        return [self.get(r[0], r[1]) for r in self.db.execute(
            "SELECT guild_id,id FROM tournament_events WHERE message_id IS NOT NULL "
            "AND state IN ('registration','running','cancelling')"
        ).fetchall()]

    def entries(self, tournament_id):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM tournament_entries WHERE tournament_id=? ORDER BY user_id", (tournament_id,)
        )]

    def pending(self, guild):
        return [self.get(guild, r[0]) for r in self.db.execute(
            "SELECT id FROM tournament_events WHERE guild_id=? AND (state IN ('registration','running','cancelling') OR needs_update=1)",
            (guild,),
        ).fetchall()]

    def pairs(self, tournament_id, round_no=None):
        sql, params = "SELECT * FROM tournament_pairs WHERE tournament_id=?", [tournament_id]
        if round_no is not None:
            sql += " AND round_no=?"
            params.append(round_no)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY round_no,position", params)]

    def pair(self, tournament_id, round_no, position):
        row = self.db.execute(
            "SELECT * FROM tournament_pairs WHERE tournament_id=? AND round_no=? AND position=?",
            (tournament_id, round_no, position),
        ).fetchone()
        if row is None:
            raise UserError("Bracket match not found. Check /tournament bracket.")
        return dict(row)

    def update(self, tournament_id, **changes):
        if not changes.keys() <= {"state", "current_round", "champion_id", "message_id"}:
            raise ValueError("Invalid tournament field")
        with self.db:
            self.db.execute("UPDATE tournament_events SET needs_update=1," + ", ".join(f"{key}=?" for key in changes)
                            + " WHERE id=?", (*changes.values(), tournament_id))

    def update_pair(self, pair, **changes):
        if not changes.keys() <= {"winner_id", "state", "blitz_id", "attempt", "note"}:
            raise ValueError("Invalid bracket field")
        with self.db:
            self.db.execute("UPDATE tournament_pairs SET " + ", ".join(f"{key}=?" for key in changes)
                            + " WHERE tournament_id=? AND round_no=? AND position=?",
                            (*changes.values(), pair["tournament_id"], pair["round_no"], pair["position"]))

    def insert_round(self, tournament_id, round_no, players):
        # Commit a whole round together so restart recovery never sees half a bracket.
        with self.db:
            for position in range(len(players) // 2):
                a, b = players[position * 2:position * 2 + 2]
                bye = a is None or b is None
                self.db.execute(
                    "INSERT INTO tournament_pairs(tournament_id,round_no,position,player_a,player_b,winner_id,state,note) "
                    "VALUES (?,?,?,?,?,?,?,?)", (tournament_id, round_no, position + 1, a, b,
                    (a or b) if bye else None, "done" if bye else "ready", "Bye" if bye else ""),
                )
            self.db.execute("UPDATE tournament_events SET current_round=?,state='running' WHERE id=?",
                            (round_no, tournament_id))

    def release(self, tournament_id, user=None):
        with self.db:
            if user is None:
                self.db.execute("DELETE FROM tournament_slots WHERE tournament_id=?", (tournament_id,))
            else:
                self.db.execute("DELETE FROM tournament_slots WHERE tournament_id=? AND user_id=?",
                                (tournament_id, user))

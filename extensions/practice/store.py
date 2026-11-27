"""Saved practice goals (one training track and one upsolving goal per member)."""
from __future__ import annotations

import json

# Append-only: entry i upgrades schema version i to i + 1.
MIGRATIONS = [
    """
CREATE TABLE practice_goals (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY(guild_id, user_id, kind)
);
""",
]


class PracticeStore:
    def __init__(self, db):
        self.db = db

    def goal(self, guild, user, kind):
        row = self.db.execute(
            "SELECT data FROM practice_goals WHERE guild_id=? AND user_id=? AND kind=?",
            (guild, user, kind),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_goal(self, guild, user, kind, data):
        with self.db:
            self.db.execute(
                "INSERT INTO practice_goals VALUES (?, ?, ?, ?) ON CONFLICT(guild_id,user_id,kind) "
                "DO UPDATE SET data=excluded.data", (guild, user, kind, json.dumps(data)),
            )

    def delete_goal(self, guild, user, kind):
        with self.db:
            self.db.execute("DELETE FROM practice_goals WHERE guild_id=? AND user_id=? AND kind=?",
                            (guild, user, kind))

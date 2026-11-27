"""Per-owner schema versions for one shared SQLite database.

Every table belongs to ``core`` or to one extension and is named ``<owner>_...``. Each owner
supplies an append-only list of migrations: entry *i* upgrades version *i* to *i + 1*. A
migration and its version bump commit together, and a migration that creates or drops another
owner's objects is rolled back.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence

REGISTRY = "core_schema"
Migration = str | Callable[[sqlite3.Connection], None]


class SchemaError(RuntimeError):
    """The database cannot be used with this code; the message explains why."""


def ensure_registry(db: sqlite3.Connection):
    with db:
        db.execute(f"CREATE TABLE IF NOT EXISTS {REGISTRY} (owner TEXT PRIMARY KEY, version INTEGER NOT NULL)")


def version(db: sqlite3.Connection, owner: str) -> int:
    row = db.execute(f"SELECT version FROM {REGISTRY} WHERE owner=?", (owner,)).fetchone()
    return row[0] if row else 0


def objects(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}


def migrate(db: sqlite3.Connection, owner: str, migrations: Sequence[Migration]) -> int:
    """Apply pending migrations for ``owner`` and return its resulting schema version."""
    ensure_registry(db)
    current, target = version(db, owner), len(migrations)
    if current > target:
        raise SchemaError(
            f"The database schema for '{owner}' is version {current}, but this code supports only "
            f"version {target}. Upgrade '{owner}' before starting, or use a new DATABASE_PATH."
        )
    prefix = f"{owner}_"
    for index in range(current, target):
        step = migrations[index]
        before = objects(db)
        if db.in_transaction:
            db.commit()
        try:
            if callable(step):
                db.execute("BEGIN IMMEDIATE")
                step(db)
            else:
                # executescript runs the script verbatim; the leading BEGIN keeps it in one transaction.
                db.executescript("BEGIN IMMEDIATE;\n" + step)
            after = objects(db)
            foreign = sorted(name for name in (after - before) | (before - after)
                             if not name.startswith(prefix))
            if foreign:
                raise SchemaError(
                    f"Migration {index + 1} of '{owner}' changed objects it does not own: {', '.join(foreign)}. "
                    f"Tables and indexes must be named '{prefix}...'."
                )
            db.execute(f"INSERT INTO {REGISTRY} VALUES (?, ?) ON CONFLICT(owner) DO UPDATE SET version=excluded.version",
                       (owner, index + 1))
            db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
    return target

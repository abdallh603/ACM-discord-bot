"""Per-owner schema versions. These replace the pre-extension v1–v4 upgrade tests."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from just_bot.core import schema
from just_bot.core.storage import Store

V1 = ["CREATE TABLE game_players (match_id TEXT NOT NULL, user_id INTEGER NOT NULL, owns_mute INTEGER NOT NULL DEFAULT 0);"]
V2 = V1 + ["ALTER TABLE game_players ADD COLUMN owns_deaf INTEGER NOT NULL DEFAULT 0;"]


class SchemaVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "bot.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        return db

    def test_new_database_records_core_version_and_defaults_servers_to_utc(self):
        store = Store(self.path)
        try:
            self.assertEqual(schema.version(store.db, "core"), 1)
            self.assertEqual(store.timezone(11).key, "UTC")
            store.start_link(10, 1, "tester", "nonce", 1000)
            store.complete_link(10, 1, "nonce", {"handle": "tester", "rating": 1200}, 100)
        finally:
            store.close()
        reopened = Store(self.path)
        try:
            self.assertEqual(reopened.member(10, 1)["handle"], "tester")
            self.assertEqual(schema.version(reopened.db, "core"), 1)
        finally:
            reopened.close()

    def test_upgrade_runs_only_new_migrations_and_preserves_rows(self):
        db = self.connect()
        self.assertEqual(schema.migrate(db, "game", V1), 1)
        with db:
            db.execute("INSERT INTO game_players(match_id,user_id,owns_mute) VALUES ('old',1,1)")
        self.assertEqual(schema.migrate(db, "game", V1), 1)  # re-running is a no-op
        self.assertEqual(schema.migrate(db, "game", V2), 2)
        self.assertEqual(db.execute("SELECT owns_mute, owns_deaf FROM game_players").fetchall(), [(1, 0)])

    def test_owners_are_versioned_independently(self):
        db = self.connect()
        schema.migrate(db, "game", V2)
        schema.migrate(db, "other", ["CREATE TABLE other_rows (id INTEGER);"])
        self.assertEqual((schema.version(db, "game"), schema.version(db, "other")), (2, 1))

    def test_database_newer_than_the_code_is_refused(self):
        db = self.connect()
        schema.migrate(db, "game", V2)
        with self.assertRaisesRegex(schema.SchemaError, "version 2, but this code supports only version 1"):
            schema.migrate(db, "game", V1)

    def test_objects_without_the_owner_prefix_roll_back(self):
        db = self.connect()
        bad = ["CREATE TABLE game_ok (id INTEGER); CREATE TABLE stolen (id INTEGER);"]
        with self.assertRaisesRegex(schema.SchemaError, "stolen"):
            schema.migrate(db, "game", bad)
        self.assertNotIn("game_ok", schema.objects(db))
        self.assertEqual(schema.version(db, "game"), 0)
        schema.migrate(db, "core", ["CREATE TABLE core_thing (id INTEGER);"])
        with self.assertRaisesRegex(schema.SchemaError, "core_thing"):
            schema.migrate(db, "game", ["DROP TABLE core_thing;"])
        self.assertIn("core_thing", schema.objects(db))

    def test_failed_migration_leaves_previous_version_and_data(self):
        db = self.connect()
        schema.migrate(db, "game", V1)
        with db:
            db.execute("INSERT INTO game_players(match_id,user_id) VALUES ('kept',1)")
        with self.assertRaises(sqlite3.OperationalError):
            schema.migrate(db, "game", V1 + ["ALTER TABLE game_players ADD COLUMN x INTEGER; INVALID SQL;"])
        self.assertEqual(schema.version(db, "game"), 1)
        self.assertEqual([r[1] for r in db.execute("PRAGMA table_info(game_players)")], ["match_id", "user_id", "owns_mute"])
        self.assertEqual(db.execute("SELECT match_id FROM game_players").fetchall(), [("kept",)])

    def test_python_callable_migrations_share_the_transaction(self):
        db = self.connect()

        def backfill(connection):
            connection.execute("CREATE TABLE game_scores (user_id INTEGER, points INTEGER)")
            connection.execute("INSERT INTO game_scores VALUES (1, 10)")

        schema.migrate(db, "game", [backfill])
        self.assertEqual(db.execute("SELECT points FROM game_scores").fetchone()[0], 10)
        self.assertEqual(schema.version(db, "game"), 1)

    def test_pre_extension_database_is_refused_with_guidance(self):
        db = self.connect()
        db.execute("CREATE TABLE members (guild_id INTEGER, user_id INTEGER)")
        db.execute("PRAGMA user_version=4")
        db.commit()
        db.close()
        with self.assertRaisesRegex(schema.SchemaError, "older RoundForge build"):
            Store(self.path)

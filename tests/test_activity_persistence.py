import sqlite3
import tempfile
import unittest
from pathlib import Path

from just_bot.activities_store import ACTIVITY_SCHEMA, Activities
from just_bot.blitz import validate_options
from just_bot.storage import SCHEMA, Store


class ActivityPersistenceTests(unittest.TestCase):
    def test_upgrade_preserves_old_jordan_timezone_and_defaults_new_servers_to_utc(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bot.sqlite3"
            legacy = SCHEMA.replace(", timezone TEXT NOT NULL DEFAULT 'UTC'", "")
            connection = sqlite3.connect(path)
            connection.executescript(legacy + ACTIVITY_SCHEMA)
            connection.execute("INSERT INTO guild_settings(guild_id) VALUES (10)")
            connection.execute("PRAGMA user_version=3")
            connection.commit()
            connection.close()
            store = Store(path)
            try:
                self.assertEqual(store.timezone(10).key, "Asia/Amman")
                self.assertEqual(store.timezone(11).key, "UTC")
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 4)
            finally:
                store.close()

    def test_upgrade_from_v2_adds_deafen_recovery_without_losing_mute_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bot.sqlite3"
            legacy = ACTIVITY_SCHEMA.replace("    owns_deaf INTEGER NOT NULL DEFAULT 0,\n", "")
            connection = sqlite3.connect(path)
            connection.executescript(SCHEMA + legacy)
            connection.execute("INSERT INTO blitz_players(match_id,user_id,handle,owns_mute) VALUES ('old',1,'tester',1)")
            connection.execute("PRAGMA user_version=2")
            connection.commit()
            connection.close()
            store = Store(path)
            try:
                player = Activities(store).players("old")[0]
                self.assertEqual((player["owns_mute"], player["owns_deaf"]), (1, 0))
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 4)
            finally:
                store.close()

    def test_upgrade_from_v1_preserves_accounts_and_adds_activity_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bot.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(SCHEMA.split("CREATE TABLE IF NOT EXISTS pending_links")[0])
            connection.execute("INSERT INTO members VALUES (10,1,'tester',1200,1300,1,1)")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
            connection.close()
            store = Store(path)
            try:
                self.assertEqual(store.member(10, 1)["handle"], "tester")
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertIsNone(Activities(store).goal(10, 1, "training"))
            finally:
                store.close()

    def test_games_mute_ownership_and_goals_survive_reopening_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bot.sqlite3"
            store = Store(path)
            data = Activities(store)
            first = data.create_match(10, 1, validate_options({}), [(1, "one"), (2, "two"), (3, "three")])
            second = data.create_match(10, 4, validate_options({}), [(4, "four"), (5, "five")])
            data.update_match(first["id"], state="active", starts_at=100, ends_at=1000, voice_id=123)
            data.update_player(first["id"], 2, owns_mute=1, original_voice=456)
            data.save_goal(10, 1, "training", {"stage": 1, "handle": "one"})
            store.close()
            reopened = Store(path)
            try:
                data = Activities(reopened)
                self.assertEqual(data.match(10, first["id"])["ends_at"], 1000)
                self.assertEqual(data.match(10, second["id"])["state"], "pending")
                player = data.players(first["id"])[1]
                self.assertEqual((player["owns_mute"], player["original_voice"]), (1, 456))
                self.assertEqual(data.goal(10, 1, "training")["stage"], 1)
            finally:
                reopened.close()

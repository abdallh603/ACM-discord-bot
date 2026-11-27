import tempfile
import unittest
from pathlib import Path

from just_bot.core.domain import UserError
from just_bot.core.storage import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def link(self, user=1, handle="tester", guild=10, rating=1200):
        self.store.start_link(guild, user, handle, "nonce", 1000)
        self.store.complete_link(guild, user, "nonce", {"handle": handle, "rating": rating}, 100)

    def test_links_are_case_insensitive_unique_and_guild_scoped(self):
        self.link()
        with self.assertRaises(UserError):
            self.store.start_link(10, 2, "TESTER", "n", 1000)
        self.link(2, "TESTER", guild=11)
        self.assertEqual(self.store.member(11, 2)["handle"], "TESTER")

    def test_pending_replacement_and_expiry_cannot_be_consumed(self):
        self.store.start_link(10, 1, "tester", "old", 200)
        self.store.start_link(10, 1, "tester", "new", 200)
        for nonce, now in [("old", 100), ("new", 200)]:
            with self.assertRaises(UserError):
                self.store.complete_link(10, 1, nonce, {"handle": "tester"}, now)
        self.assertIsNone(self.store.member(10, 1))

    def test_competing_links_have_one_winner(self):
        for user in [1, 2]:
            self.store.start_link(10, user, "tester", "nonce", 1000)
        self.store.complete_link(10, 1, "nonce", {"handle": "tester"}, 100)
        with self.assertRaises(UserError):
            self.store.complete_link(10, 2, "nonce", {"handle": "tester"}, 100)
        self.assertIsNotNone(self.store.pending(10, 2))

    def test_rating_leaderboard_handles_unrated_and_pagination(self):
        self.link(1, "unrated", rating=None)
        self.link(2, "highrated", rating=2400)
        self.link(3, "newbie", rating=800)
        rows = self.store.leaderboard(10, 1)
        self.assertEqual([r["handle"] for r in rows], ["highrated", "newbie", "unrated"])
        self.assertEqual(self.store.leaderboard(10, 2), [])
        self.assertEqual(self.store.leaderboard(11, 1), [])

    def test_timezone_and_channel_rules_survive_restart(self):
        self.store.set_timezone(10, "Asia/Amman")
        self.store.channel_rule(10, "add", 30)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.timezone(10).key, "Asia/Amman")
        self.assertEqual(self.store.timezone(11).key, "UTC")
        self.assertEqual(self.store.allowed_channels(10), frozenset({30}))
        self.assertEqual(self.store.allowed_channels(11), frozenset())

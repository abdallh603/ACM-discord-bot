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

    def daily(self, day="2026-11-20"):
        return self.store.save_daily(10, day, {"contestId": 1, "index": "A"}, 100, 1000)

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

    def test_daily_is_immutable_for_the_day(self):
        first = self.daily()
        second = self.store.save_daily(10, "2026-11-20", {"contestId": 2, "index": "B"}, 100, 1000)
        self.assertEqual(first, second)

    def test_duplicate_claims_and_handle_transfer_do_not_create_points(self):
        self.link()
        self.daily()
        self.store.claim(10, "2026-11-20", 1, "tester", 999, 200)
        with self.assertRaises(UserError):
            self.store.claim(10, "2026-11-20", 1, "tester", 1000, 200)
        self.store.unlink(10, 1)
        self.link(2, "TESTER")
        with self.assertRaises(UserError):
            self.store.claim(10, "2026-11-20", 2, "TESTER", 1001, 200)
        count = self.store.db.execute("SELECT COUNT(*) FROM daily_claims").fetchone()[0]
        self.assertEqual(count, 1)

    def test_claim_rechecks_current_link_and_deadline(self):
        self.link()
        self.daily()
        for handle, now in [("other", 200), ("tester", 1000), ("tester", 99)]:
            with self.assertRaises(UserError):
                self.store.claim(10, "2026-11-20", 1, handle, 999, now)
        self.store.unlink(10, 1)
        with self.assertRaises(UserError):
            self.store.claim(10, "2026-11-20", 1, "tester", 999, 200)

    def test_rating_leaderboard_handles_unrated_and_pagination(self):
        self.link(1, "unrated", rating=None)
        self.link(2, "highrated", rating=2400)
        self.link(3, "newbie", rating=800)
        rows = self.store.leaderboard(10, 1)
        self.assertEqual([r["handle"] for r in rows], ["highrated", "newbie", "unrated"])
        self.assertEqual(self.store.leaderboard(10, 2), [])
        self.assertEqual(self.store.leaderboard(11, 1), [])

    def test_monthly_points_do_not_leak_from_previous_month(self):
        self.link()
        for day, sid in [("2026-10-31", 99), ("2026-11-20", 100)]:
            self.daily(day)
            self.store.claim(10, day, 1, "tester", sid, 200)
        self.assertEqual(self.store.leaderboard(10, 1, "2026-11")[0]["points"], 10)
        self.assertEqual(self.store.leaderboard(10, 1, "2026-12")[0]["points"], 0)

    def test_settings_and_deliveries_survive_restart(self):
        self.store.configure(10, 111, 222, 1000, 9)
        self.store.mark_sent(10, "contest/1")
        self.store.save_role(10, "Pupil", 333)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.settings(10)["daily_rating"], 1000)
        self.assertEqual(self.store.roles(10), {"Pupil": 333})
        self.assertTrue(self.store.was_sent(10, "contest/1"))
        self.assertFalse(self.store.was_sent(11, "contest/1"))

import tempfile
import unittest
from pathlib import Path

from extensions.daily.store import MIGRATIONS, DailyStore
from just_bot.core.domain import UserError
from just_bot.core.schema import migrate
from just_bot.core.storage import Store


def daily_store(store):
    migrate(store.db, "daily", MIGRATIONS)
    return DailyStore(store)


class DailyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.store = Store(self.path)
        self.daily_store = daily_store(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def link(self, user=1, handle="tester", guild=10, rating=1200):
        self.store.start_link(guild, user, handle, "nonce", 1000)
        self.store.complete_link(guild, user, "nonce", {"handle": handle, "rating": rating}, 100)

    def daily(self, day="2026-11-20"):
        return self.daily_store.save_daily(10, day, {"contestId": 1, "index": "A"}, 100, 1000)

    def test_daily_is_immutable_for_the_day(self):
        first = self.daily()
        second = self.daily_store.save_daily(10, "2026-11-20", {"contestId": 2, "index": "B"}, 100, 1000)
        self.assertEqual(first, second)

    def test_duplicate_claims_and_handle_transfer_do_not_create_points(self):
        self.link()
        self.daily()
        self.daily_store.claim(10, "2026-11-20", 1, "tester", 999, 200)
        with self.assertRaises(UserError):
            self.daily_store.claim(10, "2026-11-20", 1, "tester", 1000, 200)
        self.store.unlink(10, 1)
        self.link(2, "TESTER")
        with self.assertRaises(UserError):
            self.daily_store.claim(10, "2026-11-20", 2, "TESTER", 1001, 200)
        count = self.store.db.execute("SELECT COUNT(*) FROM daily_claims").fetchone()[0]
        self.assertEqual(count, 1)

    def test_claim_rechecks_current_link_and_deadline(self):
        self.link()
        self.daily()
        for handle, now in [("other", 200), ("tester", 1000), ("tester", 99)]:
            with self.assertRaises(UserError):
                self.daily_store.claim(10, "2026-11-20", 1, handle, 999, now)
        self.store.unlink(10, 1)
        with self.assertRaises(UserError):
            self.daily_store.claim(10, "2026-11-20", 1, "tester", 999, 200)

    def test_monthly_points_do_not_leak_from_previous_month(self):
        self.link()
        for day, sid in [("2026-10-31", 99), ("2026-11-20", 100)]:
            self.daily(day)
            self.daily_store.claim(10, day, 1, "tester", sid, 200)
        self.assertEqual(self.daily_store.leaderboard(10, 1, "2026-11")[0]["points"], 10)
        self.assertEqual(self.daily_store.leaderboard(10, 1, "2026-12")[0]["points"], 0)

    def test_settings_and_deliveries_survive_restart(self):
        self.daily_store.configure(10, 111, 222, 1000, 9)
        self.daily_store.mark_sent(10, "contest/1")
        self.store.close()
        self.store = Store(self.path)
        self.daily_store = daily_store(self.store)
        self.assertEqual(self.daily_store.settings(10)["daily_rating"], 1000)
        self.assertTrue(self.daily_store.was_sent(10, "contest/1"))
        self.assertFalse(self.daily_store.was_sent(11, "contest/1"))

import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from extensions.daily.service import AMMAN, DailyService
from extensions.daily.store import MIGRATIONS, DailyStore
from just_bot.core.domain import UserError
from just_bot.core.schema import migrate
from just_bot.core.service import Community
from just_bot.core.storage import Store
from tests.support import problem, submission


class DailyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.cf = AsyncMock()
        self.service = Community(self.store, self.cf)
        migrate(self.store.db, "daily", MIGRATIONS)
        self.daily_store = DailyStore(self.store)
        self.daily = DailyService(self.service, self.daily_store)

    async def asyncTearDown(self):
        self.store.close()

    async def link(self):
        self.cf.users.return_value = [{"handle": "tester", "rating": 1200}]
        nonce, expires = await self.service.start_link(10, 1, "tester")
        self.cf.users.return_value = [{"handle": "tester", "rating": 1200, "firstName": nonce}]
        await self.service.verify(10, 1)
        return expires

    async def test_daily_survives_settings_changes_and_rotates_at_jordan_midnight(self):
        self.store.set_timezone(10, "Asia/Amman")
        self.cf.problems.return_value = [problem(100), problem(101)]
        now = datetime(2026, 11, 20, 23, 59, tzinfo=AMMAN)
        first = await self.daily.daily(10, now=now)
        self.daily_store.configure(10, None, None, 800, 9)
        self.assertEqual(await self.daily.daily(10, now=now), first)
        self.daily_store.configure(10, None, None, 1200, 9)
        second = await self.daily.daily(10, now=now + timedelta(minutes=1))
        self.assertNotEqual(first["problem"], second["problem"])
        self.assertEqual(second["day"], "2026-11-21")

    async def test_daily_claim_credits_once_and_blocks_duplicate(self):
        await self.link()
        self.cf.problems.return_value = [problem()]
        self.cf.submissions.return_value = [submission(int(time.time()), sid=123)]
        self.assertEqual((await self.daily.claim(10, 1))["id"], 123)
        with self.assertRaisesRegex(UserError, "already claimed"):
            await self.daily.claim(10, 1)
        month = datetime.now(AMMAN).strftime("%Y-%m")
        self.assertEqual(self.daily_store.leaderboard(10, 1, month)[0]["points"], 10)

    async def test_submission_before_link_is_not_rewarded(self):
        await self.link()
        self.cf.problems.return_value = [problem()]
        self.cf.submissions.return_value = [submission(int(time.time()) - 60)]
        with self.assertRaisesRegex(UserError, "No accepted"):
            await self.daily.claim(10, 1)

    async def test_deleted_link_during_claim_is_not_rewarded(self):
        await self.link()
        self.cf.problems.return_value = [problem()]
        async def unlink_during_lookup(*args, **kwargs):
            self.store.unlink(10, 1)
            return [submission(int(time.time()))]
        self.cf.submissions.side_effect = unlink_during_lookup
        with self.assertRaisesRegex(UserError, "account changed"):
            await self.daily.claim(10, 1)

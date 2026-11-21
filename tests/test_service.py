import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from just_bot.domain import AMMAN, UserError
from just_bot.service import Community
from just_bot.storage import Store
from test_domain import problem, submission


class CommunityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.cf = AsyncMock()
        self.service = Community(self.store, self.cf)

    async def asyncTearDown(self):
        self.store.close()

    async def link(self):
        self.cf.users.return_value = [{"handle": "tester", "rating": 1200}]
        nonce, expires = await self.service.start_link(10, 1, "tester")
        self.cf.users.return_value = [{"handle": "tester", "rating": 1200, "firstName": nonce}]
        await self.service.verify(10, 1)
        return expires

    async def test_complete_ownership_verification_flow(self):
        expires = await self.link()
        self.assertEqual(self.store.member(10, 1)["handle"], "tester")
        self.assertIsNone(self.store.pending(10, 1))
        self.assertGreater(expires, time.time())
        self.assertTrue(all(call.kwargs.get("fresh") for call in self.cf.users.await_args_list))

    async def test_wrong_profile_code_never_links(self):
        self.cf.users.return_value = [{"handle": "tester", "firstName": "someone else"}]
        await self.service.start_link(10, 1, "tester")
        with self.assertRaisesRegex(UserError, "First name"):
            await self.service.verify(10, 1)
        self.assertIsNone(self.store.member(10, 1))

    async def test_replaced_pending_code_cannot_complete_an_old_verification(self):
        self.cf.users.return_value = [{"handle": "tester"}]
        nonce, expires = await self.service.start_link(10, 1, "tester")
        async def replace_during_lookup(*args, **kwargs):
            self.store.start_link(10, 1, "tester", "replacement", expires)
            return [{"handle": "tester", "firstName": nonce}]
        self.cf.users.side_effect = replace_during_lookup
        with self.assertRaisesRegex(UserError, "replaced"):
            await self.service.verify(10, 1)
        self.assertIsNone(self.store.member(10, 1))

    async def test_recommendation_excludes_accepted_problems(self):
        await self.link()
        self.cf.submissions.return_value = [submission(int(time.time()))]
        self.cf.problems.return_value = [problem(), problem(101)]
        result = await self.service.recommend(10, 1, None, "dp")
        self.assertEqual(result["contestId"], 101)

    async def test_daily_survives_settings_changes_and_rotates_at_jordan_midnight(self):
        self.store.set_timezone(10, "Asia/Amman")
        self.cf.problems.return_value = [problem(100), problem(101)]
        now = datetime(2026, 11, 20, 23, 59, tzinfo=AMMAN)
        first = await self.service.daily(10, now=now)
        self.store.configure(10, None, None, 800, 9)
        self.assertEqual(await self.service.daily(10, now=now), first)
        self.store.configure(10, None, None, 1200, 9)
        second = await self.service.daily(10, now=now + timedelta(minutes=1))
        self.assertNotEqual(first["problem"], second["problem"])
        self.assertEqual(second["day"], "2026-11-21")

    async def test_daily_claim_credits_once_and_blocks_duplicate(self):
        await self.link()
        self.cf.problems.return_value = [problem()]
        self.cf.submissions.return_value = [submission(int(time.time()), sid=123)]
        self.assertEqual((await self.service.claim_daily(10, 1))["id"], 123)
        with self.assertRaisesRegex(UserError, "already claimed"):
            await self.service.claim_daily(10, 1)
        month = datetime.now(AMMAN).strftime("%Y-%m")
        self.assertEqual(self.store.leaderboard(10, 1, month)[0]["points"], 10)

    async def test_submission_before_link_is_not_rewarded(self):
        await self.link()
        self.cf.problems.return_value = [problem()]
        self.cf.submissions.return_value = [submission(int(time.time()) - 60)]
        with self.assertRaisesRegex(UserError, "No accepted"):
            await self.service.claim_daily(10, 1)

    async def test_deleted_link_during_claim_is_not_rewarded(self):
        await self.link()
        self.cf.problems.return_value = [problem()]
        async def unlink_during_lookup(*args, **kwargs):
            self.store.unlink(10, 1)
            return [submission(int(time.time()))]
        self.cf.submissions.side_effect = unlink_during_lookup
        with self.assertRaisesRegex(UserError, "account changed"):
            await self.service.claim_daily(10, 1)

    async def test_refresh_isolates_unavailable_profiles(self):
        await self.link()
        self.store.start_link(10, 2, "deleted", "nonce", int(time.time()) + 100)
        self.store.complete_link(10, 2, "nonce", {"handle": "deleted"}, int(time.time()))
        self.cf.users.side_effect = [UserError("missing handle"),
                                     [{"handle": "tester", "rating": 1400}], UserError("missing")]
        self.assertEqual(await self.service.refresh(10), ["deleted"])
        self.assertEqual(self.store.member(10, 1)["rating"], 1400)

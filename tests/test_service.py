import time
import unittest
from unittest.mock import AsyncMock

from just_bot.core.domain import UserError
from just_bot.core.service import Community
from just_bot.core.storage import Store
from tests.support import problem, submission


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

    async def test_refresh_isolates_unavailable_profiles(self):
        await self.link()
        self.store.start_link(10, 2, "deleted", "nonce", int(time.time()) + 100)
        self.store.complete_link(10, 2, "nonce", {"handle": "deleted"}, int(time.time()))
        self.cf.users.side_effect = [UserError("missing handle"),
                                     [{"handle": "tester", "rating": 1400}], UserError("missing")]
        self.assertEqual(await self.service.refresh(10), ["deleted"])
        self.assertEqual(self.store.member(10, 1)["rating"], 1400)

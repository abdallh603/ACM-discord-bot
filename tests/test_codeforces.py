import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from just_bot.core.codeforces import Codeforces, CodeforcesError


class CodeforcesTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_share_cache(self):
        transport = AsyncMock(return_value={"status": "OK", "result": ["value"]})
        client = Codeforces(transport=transport, interval=0)
        values = await asyncio.gather(*(client.call("test", ttl=60) for _ in range(8)))
        self.assertEqual(values, [["value"]] * 8)
        self.assertEqual(transport.await_count, 1)

    async def test_verification_bypasses_profile_cache(self):
        transport = AsyncMock(side_effect=[
            {"status": "OK", "result": [{"handle": "tester", "firstName": "old"}]},
            {"status": "OK", "result": [{"handle": "tester", "firstName": "new"}]},
        ])
        client = Codeforces(transport=transport, interval=0)
        self.assertEqual((await client.users(["tester"]))[0]["firstName"], "old")
        self.assertEqual((await client.users(["tester"], fresh=True))[0]["firstName"], "new")
        self.assertEqual((await client.users(["tester"]))[0]["firstName"], "new")
        self.assertEqual(transport.await_count, 2)

    async def test_requests_are_serialized_and_spaced(self):
        times = []
        async def transport(method, params):
            times.append(time.monotonic())
            return {"status": "OK", "result": []}
        client = Codeforces(transport=transport, interval=0.01)
        await asyncio.gather(*(client.call("test", n=n) for n in range(3)))
        self.assertGreaterEqual(times[1] - times[0], 0.009)
        self.assertGreaterEqual(times[2] - times[1], 0.009)

    async def test_call_limit_is_retried(self):
        transport = AsyncMock(side_effect=[
            {"status": "FAILED", "comment": "Call limit exceeded"},
            {"status": "OK", "result": [1]},
        ])
        client = Codeforces(transport=transport, interval=0)
        with patch("just_bot.core.codeforces.asyncio.sleep", new_callable=AsyncMock):
            self.assertEqual(await client.call("test"), [1])
        self.assertEqual(transport.await_count, 2)

    async def test_api_error_is_readable_and_not_cached(self):
        transport = AsyncMock(return_value={"status": "FAILED", "comment": "handle not found"})
        client = Codeforces(transport=transport, interval=0)
        with self.assertRaisesRegex(CodeforcesError, "handle not found"):
            await client.call("user.info", ttl=60, handles="tester")
        self.assertEqual(len(client._cache), 0)

    async def test_malformed_json_retries_and_fails_cleanly(self):
        client = Codeforces(transport=AsyncMock(side_effect=ValueError("bad JSON")), interval=0)
        with patch("just_bot.core.codeforces.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(CodeforcesError, "Could not reach"):
                await client.call("test")

    async def test_submission_history_paginates(self):
        client = Codeforces(interval=0)
        client.call = AsyncMock(side_effect=[
            [{"creationTimeSeconds": 200}] * 1000, [{"creationTimeSeconds": 150}],
        ])
        self.assertEqual(len(await client.submissions("tester")), 1001)
        self.assertEqual(client.call.await_args_list[1].kwargs["from"], 1001)

    async def test_daily_lookup_stops_after_crossing_deadline(self):
        client = Codeforces(interval=0)
        client.call = AsyncMock(return_value=[{"creationTimeSeconds": 50}] * 1000)
        self.assertEqual(len(await client.submissions("tester", since=100)), 1000)
        self.assertEqual(client.call.await_count, 1)

    async def test_incomplete_history_is_never_silently_accepted(self):
        client = Codeforces(interval=0)
        client.call = AsyncMock(return_value=[{"creationTimeSeconds": 200}] * 1000)
        with self.assertRaisesRegex(CodeforcesError, "20,000"):
            await client.submissions("tester")

    async def test_profile_requests_are_batched(self):
        client = Codeforces(interval=0)
        client.call = AsyncMock(return_value=[])
        await client.users([f"member{n}" for n in range(101)])
        self.assertEqual(client.call.await_count, 3)

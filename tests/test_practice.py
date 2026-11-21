import unittest
from unittest.mock import AsyncMock

from just_bot.activities_store import Activities
from just_bot.domain import UserError, problem_key
from just_bot.practice import Practice
from just_bot.service import Community
from just_bot.storage import Store
from test_domain import problem, submission


class PracticeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.store.start_link(10, 1, "tester", "nonce", 9999999999)
        self.store.complete_link(10, 1, "nonce", {"handle": "tester"}, 1)
        self.data = Activities(self.store)
        self.cf = AsyncMock()
        self.cf.submissions.return_value = []
        pool = [problem(100 + level * 10 + i, rating=rating, tags=["implementation"])
                for level, rating in enumerate([800, 900, 1000]) for i in range(5)]
        self.cf.problems.return_value = pool
        self.practice = Practice(Community(self.store, self.cf), self.data)

    async def asyncTearDown(self):
        self.store.close()

    async def test_training_stage_has_three_distinct_unsolved_problems(self):
        self.cf.submissions.return_value = [{**submission(1), "problem": self.cf.problems.return_value[0]}]
        goal = await self.practice.start_training(10, 1, "fundamentals")
        assigned = goal["stages"][0]["problems"]
        self.assertEqual(len({problem_key(p) for p in assigned}), 3)
        self.assertNotIn(problem_key(self.cf.problems.return_value[0]), {problem_key(p) for p in assigned})
        self.assertTrue(all(p["rating"] == 800 for p in assigned))

    async def test_training_cannot_advance_before_stage_is_solved(self):
        await self.practice.start_training(10, 1, "fundamentals")
        with self.assertRaisesRegex(UserError, "Finish all three"):
            await self.practice.training(10, 1, advance=True)
        self.assertEqual(self.data.goal(10, 1, "training")["stage"], 0)

    async def test_training_advances_and_completes_all_three_stages(self):
        goal = await self.practice.start_training(10, 1, "fundamentals")
        history = []
        for stage in range(3):
            current = goal["stages"][stage]
            history.extend({**submission(current["assigned_at"]), "problem": p}
                           for p in current["problems"])
            self.cf.submissions.return_value = history[:]
            goal, solved = await self.practice.training(10, 1, advance=True)
        self.assertTrue(goal["complete"])
        self.assertEqual(len(solved), 3)
        self.assertTrue(all(stage["completed_at"] for stage in goal["stages"]))

    async def test_training_saved_assignments_are_reused_after_service_restart(self):
        first = await self.practice.start_training(10, 1, "fundamentals")
        replacement = Practice(self.practice.community, Activities(self.store))
        second, _ = await replacement.training(10, 1)
        self.assertEqual(first, second)
        self.assertEqual(self.cf.problems.await_count, 1)

    async def test_changed_handle_cannot_inherit_another_accounts_progress(self):
        await self.practice.start_training(10, 1, "fundamentals")
        self.store.unlink(10, 1)
        self.store.start_link(10, 1, "another", "nonce", 9999999999)
        self.store.complete_link(10, 1, "nonce", {"handle": "another"}, 1)
        with self.assertRaisesRegex(UserError, "account changed"):
            await self.practice.training(10, 1)

    async def test_upsolving_rejects_ongoing_contests(self):
        self.cf.call.return_value = {"contest": {"phase": "CODING"}, "problems": []}
        with self.assertRaisesRegex(UserError, "finished"):
            await self.practice.start_upsolve(10, 1, 100)
        self.assertIsNone(self.data.goal(10, 1, "upsolve"))

    async def test_upsolving_picks_next_unsolved_problems_and_tracks_deadline(self):
        pool = [problem(100, index="A", rating=800), problem(100, index="B", rating=1000),
                problem(100, index="C", rating=1200)]
        self.cf.call.return_value = {"contest": {"name": "Finished Round", "phase": "FINISHED"},
                                     "problems": pool}
        self.cf.submissions.return_value = [{**submission(1), "problem": pool[0]}]
        goal = await self.practice.start_upsolve(10, 1, 100, hours=48)
        self.assertEqual([p["index"] for p in goal["problems"]], ["B", "C"])
        self.assertEqual(goal["ends_at"] - goal["starts_at"], 48 * 3600)
        self.cf.submissions.return_value = [
            {**submission(goal["starts_at"]), "problem": pool[1]},
            {**submission(goal["ends_at"]), "problem": pool[2]},
        ]
        _, solved = await self.practice.upsolve(10, 1)
        self.assertEqual(solved, {(100, "B")})

    async def test_stopping_one_goal_leaves_other_kind_untouched(self):
        await self.practice.start_training(10, 1, "fundamentals")
        self.data.save_goal(10, 1, "upsolve", {"test": "retained"})
        await self.practice.stop(10, 1, "training")
        self.assertIsNone(self.data.goal(10, 1, "training"))
        self.assertEqual(self.data.goal(10, 1, "upsolve"), {"test": "retained"})

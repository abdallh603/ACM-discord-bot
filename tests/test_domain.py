import random
import unittest
from datetime import date, datetime, timezone

from just_bot.core.domain import (
    AMMAN,
    UserError,
    accepted_submission,
    choose_problem,
    day_window,
    rank_for,
    upcoming_contests,
    validate_handle,
)
from tests.support import problem, submission


class DomainTests(unittest.TestCase):
    def test_all_rank_boundaries(self):
        for rating, expected in [
            (None, "Unrated"), (0, "Newbie"), (1199, "Newbie"), (1200, "Pupil"),
            (1400, "Specialist"), (1600, "Expert"), (1900, "Candidate Master"),
            (2100, "Master"), (2300, "International Master"), (2400, "Grandmaster"),
            (2600, "International Grandmaster"), (3000, "Legendary Grandmaster"),
        ]:
            with self.subTest(rating=rating):
                self.assertEqual(rank_for(rating)[0], expected)

    def test_jordan_day_boundary_is_not_utc_midnight(self):
        start, end = day_window(date(2026, 11, 20))
        self.assertEqual(datetime.fromtimestamp(start, timezone.utc).isoformat(), "2026-11-19T21:00:00+00:00")
        self.assertEqual(datetime.fromtimestamp(end, AMMAN).day, 21)
        self.assertEqual(end - start, 86400)

    def test_problem_filters_solved_special_gym_and_difficulty(self):
        pool = [problem(100), problem(101, tags=["*special"]), problem(100001),
                problem(102, rating=800), problem(103)]
        self.assertEqual(choose_problem(pool, 1200, tag="DP", excluded={(100, "A")})["contestId"], 103)
        with self.assertRaises(UserError):
            choose_problem(pool, 1200, tag="geometry")

    def test_seeded_selection_is_repeatable(self):
        pool = [problem(n) for n in range(1, 100)]
        self.assertEqual(choose_problem(pool, 1200, rng=random.Random("day")),
                         choose_problem(pool, 1200, rng=random.Random("day")))

    def test_claim_requires_accepted_solo_practice_inside_window(self):
        invalid = [submission(99), submission(200), submission(150, verdict="WRONG_ANSWER"),
                   submission(150, kind="CONTESTANT"), submission(150, members=2)]
        self.assertIsNone(accepted_submission(invalid, problem(), 100, 200))
        valid = submission(100, sid=9)
        self.assertEqual(accepted_submission(invalid + [valid], problem(), 100, 200)["id"], 9)

    def test_contests_are_future_and_sorted(self):
        contests = [{"id": 1, "phase": "BEFORE", "startTimeSeconds": 300},
                    {"id": 2, "phase": "BEFORE", "startTimeSeconds": 200},
                    {"id": 3, "phase": "CODING", "startTimeSeconds": 150},
                    {"id": 4, "phase": "BEFORE", "startTimeSeconds": 50}]
        self.assertEqual([c["id"] for c in upcoming_contests(contests, 100)], [2, 1])

    def test_handle_validation(self):
        self.assertEqual(validate_handle("  tourist  "), "tourist")
        for invalid in ["a", "a/b", "a;b", "a b", "x" * 25, "@everyone"]:
            with self.subTest(handle=invalid), self.assertRaises(UserError):
                validate_handle(invalid)

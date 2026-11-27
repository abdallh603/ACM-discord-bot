import unittest
from datetime import date, datetime, timezone

from extensions.daily.service import AMMAN, day_window, upcoming_contests


class CalendarTests(unittest.TestCase):
    def test_jordan_day_boundary_is_not_utc_midnight(self):
        start, end = day_window(date(2026, 11, 20))
        self.assertEqual(datetime.fromtimestamp(start, timezone.utc).isoformat(), "2026-11-19T21:00:00+00:00")
        self.assertEqual(datetime.fromtimestamp(end, AMMAN).day, 21)
        self.assertEqual(end - start, 86400)

    def test_contests_are_future_and_sorted(self):
        contests = [{"id": 1, "phase": "BEFORE", "startTimeSeconds": 300},
                    {"id": 2, "phase": "BEFORE", "startTimeSeconds": 200},
                    {"id": 3, "phase": "CODING", "startTimeSeconds": 150},
                    {"id": 4, "phase": "BEFORE", "startTimeSeconds": 50}]
        self.assertEqual([c["id"] for c in upcoming_contests(contests, 100)], [2, 1])

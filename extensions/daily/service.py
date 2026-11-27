"""The server's daily problem (seeded per server and day) and verified daily claims."""
from __future__ import annotations

import random
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from zoneinfo import ZoneInfo

from just_bot.core.api import UserError, accepted_submission, choose_problem

from .store import DailyStore

AMMAN = ZoneInfo("Asia/Amman")


def day_window(day: date, zone=AMMAN) -> tuple[int, int]:
    start = datetime.combine(day, clock.min, zone)
    end = datetime.combine(day + timedelta(days=1), clock.min, zone)
    return int(start.timestamp()), int(end.timestamp())


def upcoming_contests(contests: list[dict], now: int) -> list[dict]:
    return sorted(
        [c for c in contests if c.get("phase") == "BEFORE"
         and c.get("startTimeSeconds", 0) > now],
        key=lambda c: c["startTimeSeconds"],
    )


class DailyService:
    def __init__(self, community, store: DailyStore):
        self.community = community
        self.cf = community.cf
        self.store = store

    async def daily(self, guild: int, *, now: datetime | None = None):
        zone = self.store.core.timezone(guild)
        now = (now or datetime.now(zone)).astimezone(zone)
        day = now.date()
        stored = self.store.daily(guild, day.isoformat())
        if stored:
            return stored
        config = self.store.settings(guild)
        excluded = self.store.recent_daily_keys(guild, (day - timedelta(days=30)).isoformat())
        problems = sorted(
            await self.cf.problems(),
            key=lambda p: (p.get("contestId") or 0, p.get("index") or ""),
        )
        problem = choose_problem(
            problems, config["daily_rating"], excluded=excluded,
            rng=random.Random(f"RoundForge/{guild}/{day.isoformat()}"),
        )
        return self.store.save_daily(guild, day.isoformat(), problem, *day_window(day, zone))

    async def claim(self, guild: int, user: int):
        member = self.community.require_member(guild, user)
        daily = await self.daily(guild)
        start = max(daily["starts_at"], member["verified_at"])
        submissions = await self.cf.submissions(member["handle"], since=start)
        submission = accepted_submission(submissions, daily["problem"], start, daily["ends_at"])
        if not submission:
            raise UserError(
                "No accepted individual PRACTICE submission found for today's problem after "
                "your handle was verified. Solve it before the server's daily deadline and try again. "
                "Codeforces results may take a moment to update."
            )
        self.store.claim(
            guild, daily["day"], user, member["handle"], submission["id"], int(time.time())
        )
        return submission

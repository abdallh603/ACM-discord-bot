from __future__ import annotations

import random
import secrets
import time
from datetime import datetime, timedelta

from .codeforces import Codeforces
from .domain import (
    AMMAN,
    UserError,
    accepted_submission,
    choose_problem,
    day_window,
    problem_key,
    validate_handle,
)
from .storage import Store


class Community:
    def __init__(self, store: Store, cf: Codeforces):
        self.store = store
        self.cf = cf

    def require_member(self, guild: int, user: int):
        member = self.store.member(guild, user)
        if not member:
            raise UserError("Link and verify your Codeforces account with /handle link first.")
        return member

    async def start_link(self, guild: int, user: int, handle: str):
        handle = validate_handle(handle)
        profile = (await self.cf.users([handle], fresh=True))[0]
        nonce = "RF-" + secrets.token_hex(8)
        expires = int(time.time()) + 600
        self.store.start_link(guild, user, profile["handle"], nonce, expires)
        return nonce, expires

    async def verify(self, guild: int, user: int):
        pending = self.store.pending(guild, user)
        if not pending or pending["expires_at"] <= int(time.time()):
            raise UserError("No active verification. Run /handle link again.")
        profile = (await self.cf.users([pending["handle"]], fresh=True))[0]
        if profile.get("firstName", "").strip() != pending["nonce"]:
            raise UserError(
                "The verification code is not in your Codeforces First name field yet. "
                "Save your profile settings, wait a few seconds, then use /handle verify."
            )
        self.store.complete_link(guild, user, pending["nonce"], profile, int(time.time()))
        return profile

    async def recommend(self, guild: int, user: int, rating: int | None, tag: str | None):
        member = self.require_member(guild, user)
        target = rating if rating is not None else max(800, ((member["rating"] or 800) // 100) * 100)
        submissions = await self.cf.submissions(member["handle"])
        solved = {problem_key(s.get("problem", {})) for s in submissions if s.get("verdict") == "OK"}
        return choose_problem(await self.cf.problems(), target, tag=tag, excluded=solved)

    async def daily(self, guild: int, *, now: datetime | None = None):
        zone = self.store.timezone(guild)
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

    async def claim_daily(self, guild: int, user: int):
        member = self.require_member(guild, user)
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

    async def refresh(self, guild: int):
        members = self.store.members(guild)
        # Isolate renamed/deleted handles so one unavailable account cannot block the rest.
        failures = []
        for offset in range(0, len(members), 50):
            batch = members[offset:offset + 50]
            try:
                profiles = await self.cf.users([member["handle"] for member in batch])
            except UserError:
                profiles = []
                for member in batch:
                    try:
                        profiles.extend(await self.cf.users([member["handle"]]))
                    except UserError:
                        failures.append(member["handle"])
            for profile in profiles:
                self.store.update_profile(guild, profile, int(time.time()))
        return failures

from __future__ import annotations

import secrets
import time

from .codeforces import Codeforces
from .domain import UserError, choose_problem, problem_key, validate_handle
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

"""Beginner training tracks and post-contest upsolving goals with saved progress."""
from __future__ import annotations

import asyncio
import random
import time

from just_bot.core.api import UserError, accepted_submission, problem_key

from .store import PracticeStore

TRACKS = {
    "fundamentals": {
        "title": "Programming fundamentals", "tag": "implementation", "ratings": [800, 900, 1000],
        "tips": ["Translate the statement into small steps; test the smallest valid input.",
                 "Write down the state you maintain and test boundary cases.",
                 "Estimate time complexity before coding; compare against a brute-force solution."],
    },
    "greedy": {
        "title": "Greedy algorithms", "tag": "greedy", "ratings": [900, 1100, 1300],
        "tips": ["What local choice looks best? Try to construct a counterexample.",
                 "Look for a sorting order; explain why swapping adjacent choices cannot help.",
                 "State the invariant that makes every greedy choice safe."],
    },
    "binary_search": {
        "title": "Binary search", "tag": "binary search", "ratings": [1000, 1200, 1400],
        "tips": ["Define your search interval and what each endpoint means.",
                 "Can you answer a yes/no question for a proposed answer? Prove monotonicity.",
                 "Separate the feasibility predicate from the search; check overflow and off-by-one errors."],
    },
    "dp": {
        "title": "Dynamic programming", "tag": "dp", "ratings": [1000, 1200, 1400],
        "tips": ["Define a state in one sentence and list its base cases.",
                 "Derive transitions from the last decision; work out a small example by hand.",
                 "Count states times transitions; consider whether earlier layers can be discarded."],
    },
}


class Practice:
    def __init__(self, community, store: PracticeStore):
        self.community = community
        self.cf = community.cf
        self.store = store
        self.lock = asyncio.Lock()

    async def _history(self, guild, user, goal=None):
        member = self.community.require_member(guild, user)
        if goal and member["handle"].casefold() != goal["handle"].casefold():
            raise UserError("Your linked account changed. Stop this goal and start a new one.")
        return member, await self.cf.submissions(member["handle"])

    async def _stage(self, track, stage, history, previous):
        spec = TRACKS[track]
        excluded = {problem_key(s["problem"]) for s in history if s.get("verdict") == "OK"}
        excluded |= {problem_key(p) for entry in previous for p in entry["problems"]}
        pool = [p for p in await self.cf.problems()
                if p.get("contestId") and p["contestId"] < 100000 and p.get("rating") == spec["ratings"][stage]
                and spec["tag"] in p.get("tags", []) and "*special" not in p.get("tags", [])
                and p.get("type", "PROGRAMMING") == "PROGRAMMING" and problem_key(p) not in excluded]
        if len(pool) < 3:
            raise UserError("Fewer than three unsolved problems are available for this stage. Try another track.")
        return {"assigned_at": int(time.time()), "problems": random.sample(pool, 3), "completed_at": None}

    async def start_training(self, guild, user, track):
        if track not in TRACKS:
            raise UserError("Choose fundamentals, greedy, binary_search, or dp.")
        async with self.lock:
            if self.store.goal(guild, user, "training"):
                raise UserError("You already have a training track. Use /training stop before replacing it.")
            member, history = await self._history(guild, user)
            stage = await self._stage(track, 0, history, [])
            goal = {"track": track, "handle": member["handle"], "stage": 0, "stages": [stage], "complete": False}
            self._check_link(guild, user, goal)
            self.store.save_goal(guild, user, "training", goal)
            return goal

    def _check_link(self, guild, user, goal):
        current = self.community.require_member(guild, user)
        if current["handle"].casefold() != goal["handle"].casefold():
            raise UserError("Your linked account changed while checking your progress. Try again.")

    async def training(self, guild, user, *, advance=False):
        async with self.lock:
            goal = self.store.goal(guild, user, "training")
            if not goal:
                raise UserError("Start a track with /training start first.")
            _, history = await self._history(guild, user, goal)
            stage = goal["stages"][goal["stage"]]
            solved = [problem_key(p) for p in stage["problems"]
                      if accepted_submission(history, p, stage["assigned_at"], int(time.time()) + 1)]
            if advance:
                if len(solved) != len(stage["problems"]):
                    raise UserError("Finish all three stage problems in individual practice before advancing.")
                stage["completed_at"] = int(time.time())
                if goal["stage"] == len(TRACKS[goal["track"]]["ratings"]) - 1:
                    goal["complete"] = True
                else:
                    goal["stage"] += 1
                    goal["stages"].append(await self._stage(goal["track"], goal["stage"], history, goal["stages"]))
                    solved = []
            self._check_link(guild, user, goal)
            self.store.save_goal(guild, user, "training", goal)
            return goal, set(solved)

    async def start_upsolve(self, guild, user, contest_id, hours=48, count=3):
        if not 1 <= contest_id < 100000 or not 1 <= hours <= 168 or not 1 <= count <= 5:
            raise UserError("Use a regular CF contest, 1–168 hours, and 1–5 problems.")
        async with self.lock:
            existing = self.store.goal(guild, user, "upsolve")
            if existing and existing["ends_at"] > int(time.time()):
                raise UserError("You already have an upsolving goal. Use /upsolve stop to replace it.")
            result = await self.cf.call("contest.standings", contestId=contest_id,
                                        **{"from": 1, "count": 1}, ttl=120)
            contest = result["contest"]
            if contest.get("phase") != "FINISHED":
                raise UserError("Upsolving goals are available only after the contest has finished.")
            member, history = await self._history(guild, user)
            solved = {problem_key(s["problem"]) for s in history if s.get("verdict") == "OK"}
            candidates = [p for p in result["problems"] if problem_key(p) not in solved
                          and p.get("type", "PROGRAMMING") == "PROGRAMMING"
                          and "*special" not in p.get("tags", [])]
            candidates.sort(key=lambda p: (p.get("rating") or 9999, p["index"]))
            if not candidates:
                raise UserError("You already solved every supported problem in this contest.")
            now = int(time.time())
            goal = {"handle": member["handle"], "contest_id": contest_id, "contest_name": contest["name"],
                    "starts_at": now, "ends_at": now + hours * 3600, "problems": candidates[:count]}
            self._check_link(guild, user, goal)
            self.store.save_goal(guild, user, "upsolve", goal)
            return goal

    async def upsolve(self, guild, user):
        async with self.lock:
            goal = self.store.goal(guild, user, "upsolve")
            if not goal:
                raise UserError("Start a post-contest goal with /upsolve start.")
            _, history = await self._history(guild, user, goal)
            self._check_link(guild, user, goal)
            solved = {problem_key(p) for p in goal["problems"]
                      if accepted_submission(history, p, goal["starts_at"], goal["ends_at"])}
            return goal, solved

    async def stop(self, guild, user, kind):
        async with self.lock:
            self.store.delete_goal(guild, user, kind)

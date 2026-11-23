"""Multiplayer blitz rules and durable lifecycle, with a Discord adapter."""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from urllib.parse import urlparse

from .blitz_options import TOPICS, voice_settings
from .domain import UserError, accepted_submission, problem_key, within_window

log = logging.getLogger(__name__)
TERMINAL = {"finished", "cancelled", "timed_out", "unresolved"}


def parse_problem(value: str) -> tuple[int, str]:
    value = value.strip()
    if "://" in value:
        url = urlparse(value)
        if url.scheme != "https" or url.hostname not in {"codeforces.com", "www.codeforces.com"}:
            raise UserError("Use a Codeforces problem URL or an ID such as 4A.")
        match = re.fullmatch(r"/(?:problemset/problem/(\d+)/([A-Za-z][0-9]?)|contest/(\d+)/problem/([A-Za-z][0-9]?))/?", url.path)
        if not match:
            raise UserError("Use a public Codeforces contest/problem URL.")
        contest, index = match.group(1, 2) if match.group(1) else match.group(3, 4)
    else:
        match = re.fullmatch(r"(\d+)[/ -]?([A-Za-z][0-9]?)", value)
        if not match:
            raise UserError("Use a problem ID such as 4A, 1700/C, or a Codeforces problem URL.")
        contest, index = match.groups()
    if not 1 <= int(contest) < 100000:
        raise UserError("Blitz currently supports regular public Codeforces contests.")
    return int(contest), index.upper()


def validate_options(options):
    options = dict(options)
    mode = options.pop("voice_mode", None)
    if mode is not None:
        options.update(voice_settings(mode))
    result = {"minutes": None, "rating_min": None, "rating_max": None, "tag": None,
              "random_topic": False, "manual": None, "end_rule": "first_solve",
              "mute_voice": True, "deaf_voice": False, "move_voice": True, "revision": 1,
              "tags": [], "show_standings": True, **options}
    if result["minutes"] is not None and not 1 <= result["minutes"] <= 180:
        raise UserError("Blitz duration must be 1–180 minutes, or omitted for unlimited time.")
    for key in ("rating_min", "rating_max"):
        rating = result[key]
        if rating is not None and (not 800 <= rating <= 3500 or rating % 100):
            raise UserError("Ratings must be multiples of 100 between 800 and 3500.")
    if result["rating_min"] and result["rating_max"] and result["rating_min"] > result["rating_max"]:
        raise UserError("Minimum rating must not exceed maximum rating.")
    if result["tag"]:
        result["tag"] = result["tag"].strip().lower()
        if not result["tag"] or len(result["tag"]) > 50:
            raise UserError("Use a Codeforces topic such as dp, greedy, or binary search.")
    tags = result["tags"]
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise UserError("Choose topics from the topic menu.")
    result["tags"] = sorted({t.strip().lower() for t in tags})
    if any(t not in TOPICS for t in result["tags"]):
        raise UserError("Choose supported Codeforces topics from the menu.")
    if (result["tag"] or result["tags"]) and result["random_topic"]:
        raise UserError("Choose either a selected topic or a random topic.")
    if result["manual"]:
        parse_problem(result["manual"])
        if result["tag"] or result["tags"] or result["random_topic"] or result["rating_min"] or result["rating_max"]:
            raise UserError("For a manual problem, clear rating and topic filters first.")
    if result["end_rule"] not in {"first_solve", "all_solve"}:
        raise UserError("Choose first_solve or all_solve.")
    if result.get("manager_id") is not None and (not isinstance(result["manager_id"], int) or result["manager_id"] <= 0):
        raise UserError("Choose a server member as the blitz manager.")
    return result


def solve_outcome(players):
    solved = [p for p in players if p["solved_at"] is not None]
    if not solved:
        return None, "No verified solve before the deadline."
    best = min(p["solved_at"] for p in solved)
    winners = [p for p in solved if p["solved_at"] == best]
    if len(winners) > 1:
        return None, "Draw: the fastest players submitted at the same Codeforces timestamp."
    return winners[0]["user_id"], "Earliest accepted individual practice submission."


class Blitz:
    def __init__(self, community, activities, platform):
        self.community = community
        self.cf = community.cf
        self.data = activities
        self.platform = platform
        self.creation_lock = asyncio.Lock()
        self.locks = {}
        self.polls = {}
        self.clocks = {}
        self.last_poll = {}
        self.startups = {}

    async def notify_lobby(self, match):
        """Presentation failures must not prevent cleanup or strand a match."""
        callback = getattr(self.platform, "update_lobby", None)
        if callback:
            try:
                await callback(match)
            except Exception:
                log.warning("Could not refresh blitz invitation %s", match["id"], exc_info=True)

    def game_lock(self, match_id):
        return self.locks.setdefault(match_id, asyncio.Lock())

    @staticmethod
    def managers(match):
        return {match["creator_id"], match["options"].get("manager_id") or match["creator_id"]}

    def can_access(self, match, user):
        return user in self.managers(match) or user in {p["user_id"] for p in self.data.players(match["id"])}

    async def challenge(self, guild, creator, opponents, options):
        users = [creator, *opponents]
        if not 2 <= len(users) <= 8 or len(set(users)) != len(users):
            raise UserError("Choose 1–7 other distinct members, for 2–8 players total.")
        options = validate_options(options)
        async with self.creation_lock:
            players = [(user, self.community.require_member(guild, user)["handle"])
                       for user in users]
            return self.data.create_match(guild, creator, options, players)

    async def solo(self, guild, user, options):
        # Use the normal startup, selection, polling, and recovery path with one real member.
        options = validate_options({**options, "solo": True, "end_rule": "first_solve"})
        async with self.creation_lock:
            member = self.community.require_member(guild, user)
            match = self.data.create_match(guild, user, options, [(user, member["handle"])])
        return await self.accept(guild, match["id"], user)

    async def host(self, guild, host, players, options):
        if not 2 <= len(players) <= 8 or len(set(players)) != len(players):
            raise UserError("Mention 2–8 distinct players for a hosted game.")
        options = validate_options({**options, "hosted": True})
        async with self.creation_lock:
            roster = [(user, self.community.require_member(guild, user)["handle"]) for user in players]
            return self.data.create_match(guild, host, options, roster)

    async def edit(self, guild, match_id, actor, options):
        async with self.game_lock(match_id):
            match = self.data.match(guild, match_id)
            if actor not in self.managers(match) or match["state"] != "pending":
                raise UserError("Only a game manager can edit its pending invitation. Cancel an active match to change its rules.")
            if match["options"].get("tournament_id"):
                raise UserError("Tournament match rules are fixed. Cancel and use /tournament retry if needed.")
            if match["invite_expires"] <= int(time.time()):
                raise UserError("This invitation expired. Start a new challenge.")
            updated = validate_options({**match["options"], **options,
                                        "revision": match["options"]["revision"] + 1})
            self.data.update_match(match_id, options=updated)
            for player in self.data.players(match_id):
                self.data.update_player(match_id, player["user_id"],
                                        accepted_revision=updated["revision"] if player["user_id"] == actor else 0)
            match = self.data.match(guild, match_id)
            await self.notify_lobby(match)
            return match

    async def select_problem(self, match):
        options = match["options"]
        excluded = set()
        for player in self.data.players(match["id"]):
            member = self.community.require_member(match["guild_id"], player["user_id"])
            if member["handle"].casefold() != player["handle"].casefold():
                raise UserError("A participant's linked handle changed. Start a new challenge.")
            history = await self.cf.submissions(player["handle"])
            excluded |= {problem_key(s.get("problem", {})) for s in history if s.get("verdict") == "OK"}
        if options["manual"]:
            contest_id, index = parse_problem(options["manual"])
            result = await self.cf.call("contest.standings", contestId=contest_id,
                                        **{"from": 1, "count": 1}, ttl=120)
            if result["contest"].get("phase") != "FINISHED":
                raise UserError("Choose a problem from a finished contest.")
            pool = [p for p in result["problems"] if p["index"].upper() == index]
        else:
            finished = {c["id"] for c in await self.cf.contests() if c.get("phase") == "FINISHED"}
            ratings = [self.community.require_member(match["guild_id"], p["user_id"])["rating"] or 800
                       for p in self.data.players(match["id"])]
            default = max(800, min(3500, min(ratings) // 100 * 100))
            low = options["rating_min"] or options["rating_max"] or default
            high = options["rating_max"] or options["rating_min"] or default
            pool = [p for p in await self.cf.problems() if p.get("contestId") in finished
                    and isinstance(p.get("rating"), int) and low <= p["rating"] <= high]
        pool = [p for p in pool if problem_key(p) not in excluded
                and "*special" not in p.get("tags", []) and p.get("type", "PROGRAMMING") == "PROGRAMMING"]
        topics = set(options.get("tags", [])) | ({options["tag"]} if options["tag"] else set())
        if topics:
            pool = [p for p in pool if topics.intersection(p.get("tags", []))]
        if options["random_topic"]:
            topics = sorted({tag for p in pool for tag in p.get("tags", []) if not tag.startswith("*")})
            if topics:
                selected = random.choice(topics)
                pool = [p for p in pool if selected in p.get("tags", [])]
            else:
                raise UserError("No eligible problems have topics. Change the difficulty filters.")
        if not pool:
            raise UserError("No eligible problem: it must be finished, supported, and unsolved by every player. Change the filters.")
        return random.choice(pool)

    async def accept(self, guild, match_id, actor, revision=1):
        async with self.game_lock(match_id):
            match = self.data.match(guild, match_id)
            players = self.data.players(match_id)
            if actor not in {p["user_id"] for p in players} or match["state"] != "pending":
                raise UserError("Only invited players can accept a pending game.")
            if revision != match["options"]["revision"]:
                raise UserError("Settings changed. Review /blitz status and accept its current revision number.")
            if match["invite_expires"] <= int(time.time()):
                await self._finish(match, "cancelled", "Invitation expired.")
                raise UserError("This invitation expired.")
            self.data.update_player(match_id, actor, accepted_revision=revision)
            if not all(p["accepted_revision"] == revision for p in self.data.players(match_id)):
                match = self.data.match(guild, match_id)
                await self.notify_lobby(match)
                return match
            self.data.update_match(match_id, state="starting")
        self.startups[match_id] = asyncio.current_task()
        try:
            await self.notify_lobby(self.data.match(guild, match_id))
            # API history scans must never hold up another match's deadline cleanup.
            await self.platform.preflight(match)
            problem = await self.select_problem(match)
            async with self.game_lock(match_id):
                match = self.data.match(guild, match_id)
                if match["state"] != "starting":
                    raise UserError("This match was cancelled while its problem was being selected.")
                self.data.update_match(match_id, problem=problem)
                match = self.data.match(guild, match_id)
                await self.platform.create_rooms(match)
                self.data.update_match(match_id, state="countdown")
            await self.notify_lobby(self.data.match(guild, match_id))
            await self.platform.countdown(self.data.match(guild, match_id))
            async with self.game_lock(match_id):
                match = self.data.match(guild, match_id)
                if match["state"] != "countdown":
                    return match
                for player in self.data.players(match_id):
                    await self.platform.enter(match, player)
                starts = int(time.time())
                self.data.update_match(match_id, starts_at=starts,
                                       ends_at=starts + match["options"]["minutes"] * 60
                                       if match["options"]["minutes"] is not None else None)
                await self.platform.announce_start(self.data.match(guild, match_id))
                self.data.update_match(match_id, state="active")
                await self.notify_lobby(self.data.match(guild, match_id))
        except asyncio.CancelledError:
            async with self.game_lock(match_id):
                match = self.data.match(guild, match_id)
                if match["state"] not in TERMINAL:
                    await self._finish(match, "cancelled", "Match startup interrupted.")
            raise
        except Exception:
            async with self.game_lock(match_id):
                match = self.data.match(guild, match_id)
                if match["state"] not in TERMINAL:
                    await self._finish(match, "cancelled", "Could not start the match.")
            raise
        finally:
            self.startups.pop(match_id, None)
        return self.data.match(guild, match_id)

    async def cancel(self, guild, match_id, actor, *, organizer=False):
        async with self.game_lock(match_id):
            match = self.data.match(guild, match_id)
            if not self.can_access(match, actor) and not organizer:
                raise UserError("Only a participant or an organizer can cancel this match.")
            if match["state"] not in TERMINAL:
                await self._finish(match, "cancelled", "Cancelled by a participant or organizer.")
            else:
                await self._cleanup(match)

    async def set_time(self, guild, match_id, minutes, *, actor=None, organizer=False):
        if minutes is not None and not 1 <= minutes <= 180:
            raise UserError("Use 1–180 minutes, or omit minutes for unlimited time.")
        async with self.game_lock(match_id):
            match = self.data.match(guild, match_id)
            if actor is not None and actor not in self.managers(match) and not organizer:
                raise UserError("Only this game's managers or server organizers can change its timer.")
            if match["state"] != "active":
                raise UserError("Only an active game's timer can be changed; edit pending invites instead.")
            options = {**match["options"], "minutes": minutes}
            deadline = match["starts_at"] + minutes * 60 if minutes is not None else None
            self.data.update_match(match_id, options=options, ends_at=deadline)
            for player in self.data.players(match_id):
                if deadline is not None and player["solved_at"] is not None and player["solved_at"] >= deadline:
                    self.data.update_player(match_id, player["user_id"], solved_at=None)
            if deadline is not None and deadline <= int(time.time()):
                self.data.update_match(match_id, state="settling")
                await self._cleanup(self.data.match(guild, match_id))
            return self.data.match(guild, match_id)

    def request_check(self, guild, match_id):
        match = self.data.match(guild, match_id)
        if match["state"] not in {"active", "settling"}:
            raise UserError("This game is not running. Use /blitz status.")
        if not self.polls.get(match_id) or self.polls[match_id].done():
            self.polls[match_id] = asyncio.create_task(self.poll(guild, match_id))

    async def _cleanup(self, match):
        try:
            await self.platform.cleanup(match)
        except Exception:
            log.exception("Blitz cleanup deferred for %s", match["id"])
        if match["state"] in TERMINAL:
            self.data.free_slots(match["id"])

    async def _finish(self, match, state, outcome, winner=None):
        self.data.update_match(match["id"], state=state, outcome=outcome, winner_id=winner,
                               closed_at=int(time.time()))
        match = self.data.match(match["guild_id"], match["id"])
        await self._cleanup(match)
        await self.notify_lobby(self.data.match(match["guild_id"], match["id"]))

    async def recover(self, guild):
        # A match interrupted during setup is cancelled; active matches retain their deadline.
        for snapshot in self.data.matches(guild):
            async with self.game_lock(snapshot["id"]):
                match = self.data.match(guild, snapshot["id"])
                if match["state"] in {"starting", "countdown"}:
                    await self._finish(match, "cancelled", "Interrupted during match setup; recovered after restart.")
                elif match["state"] == "active":
                    if match["ends_at"] is not None and int(time.time()) >= match["ends_at"]:
                        self.data.update_match(match["id"], state="settling")
                        await self._cleanup(self.data.match(guild, match["id"]))
                    else:
                        for player in self.data.players(match["id"]):
                            if player["solved_at"] is None:
                                try:
                                    await self.platform.enter(match, player)
                                except Exception:
                                    log.exception("Blitz %s: voice policy recovery failed for %s",
                                                  match["id"], player["user_id"])
                        await self._cleanup(match)
                elif match["state"] in TERMINAL:
                    await self._cleanup(match)

    async def voice_changed(self, guild, user):
        slot = self.data.slot(guild, user)
        if not slot:
            return
        async with self.game_lock(slot["match_id"]):
            match = self.data.match(guild, slot["match_id"])
            player = next(p for p in self.data.players(match["id"]) if p["user_id"] == user)
            if (match["state"] == "active" and (match["ends_at"] is None or int(time.time()) < match["ends_at"])
                    and player["solved_at"] is None):
                await self.platform.enter(match, player)
            elif match["state"] != "pending":
                await self._cleanup(match)

    async def tick(self, guild):
        # Do not let a slow Discord operation in one game hold up other games' clocks.
        for match in self.data.matches(guild):
            mid = match["id"]
            if not self.clocks.get(mid) or self.clocks[mid].done():
                self.clocks[mid] = asyncio.create_task(self._tick_safely(guild, mid))
        await asyncio.sleep(0)

    async def _tick_safely(self, guild, match_id):
        try:
            await self.tick_match(guild, match_id)
        except Exception:
            log.exception("Blitz clock failed for %s; will retry", match_id)

    async def tick_match(self, guild, match_id):
        async with self.game_lock(match_id):
            now = int(time.time())
            match = self.data.match(guild, match_id)
            mid = match["id"]
            if match["state"] == "pending" and match["invite_expires"] <= now:
                await self._finish(match, "cancelled", "Invitation expired.")
            elif match["state"] == "active" and match["ends_at"] is not None and match["ends_at"] <= now:
                self.data.update_match(mid, state="settling")
                # Release voices before making any network request to Codeforces.
                await self._cleanup(self.data.match(guild, mid))
            match = self.data.match(guild, mid)
            if match["state"] in {"active", "settling"}:
                if match["state"] == "settling" and match["ends_at"] is not None and now >= match["ends_at"] + 120:
                    # A complete successful poll can settle a draw; API failures cannot.
                    task = self.polls.get(mid)
                    if task and not task.done():
                        task.cancel()
                    players = self.data.players(mid)
                    winner, outcome = solve_outcome(players)
                    last = self.last_poll.get(mid, {})
                    # Every query in a post-deadline pass starts after the eligible
                    # submission window closed. This also confirms partial all_solve
                    # results, which need not wait for every player to finish.
                    reliable = (last.get("complete", False)
                                and last.get("observed_after", 0) >= match["ends_at"])
                    pending = self.last_poll.get(mid, {}).get("pending", False)
                    if not reliable or pending:
                        await self._finish(match, "unresolved", "Verification was unavailable or judging is still pending. Voices released.")
                    else:
                        await self._finish(match, "finished" if winner else "timed_out", outcome, winner)
                elif not self.polls.get(mid) or self.polls[mid].done():
                    self.polls[mid] = asyncio.create_task(self.poll(guild, mid))
            elif match["state"] in TERMINAL:
                await self._cleanup(match)

    async def poll(self, guild, match_id, *, confirmed=False):
        match = self.data.match(guild, match_id)
        if match["state"] not in {"active", "settling"}:
            return
        observed_after = int(time.time())
        results = {}
        pending_stamps = []
        complete = True
        confirm_candidate = False
        try:
            for player in self.data.players(match_id):
                try:
                    history = await self.cf.submissions(player["handle"], since=match["starts_at"], fresh=True)
                except UserError:
                    complete = False
                    log.warning("Blitz %s: CF lookup unavailable for %s", match_id, player["user_id"])
                    continue
                results[player["user_id"]] = accepted_submission(history, match["problem"], match["starts_at"], match["ends_at"])
                accepted = results[player["user_id"]]
                if accepted and match["options"]["end_rule"] == "all_solve":
                    async with self.game_lock(match_id):
                        current = self.data.match(guild, match_id)
                        stamp = accepted["creationTimeSeconds"]
                        if current["state"] in {"active", "settling"} and within_window(stamp, current["starts_at"], current["ends_at"]):
                            self.data.update_player(match_id, player["user_id"], solved_at=stamp)
                            # Release this solver before waiting on another player's API request.
                            await self._cleanup(current)
                pending_stamps.extend(s["creationTimeSeconds"] for s in history
                    if problem_key(s.get("problem", {})) == problem_key(match["problem"])
                    and within_window(s.get("creationTimeSeconds", 0), match["starts_at"], match["ends_at"])
                    and s.get("author", {}).get("participantType") == "PRACTICE"
                    and len(s.get("author", {}).get("members", [])) == 1
                    and s.get("verdict") in {None, "TESTING"})
            async with self.game_lock(match_id):
                match = self.data.match(guild, match_id)
                if match["state"] not in {"active", "settling"}:
                    return
                pending_stamps = [s for s in pending_stamps if within_window(s, match["starts_at"], match["ends_at"])]
                self.last_poll[match_id] = {"complete": complete, "pending": bool(pending_stamps),
                                            "checked_at": int(time.time()), "observed_after": observed_after,
                                            "confirmed": confirmed}
                for user, accepted in results.items():
                    if accepted and within_window(accepted["creationTimeSeconds"], match["starts_at"], match["ends_at"]):
                        self.data.update_player(match_id, user, solved_at=accepted["creationTimeSeconds"])
                players = self.data.players(match_id)
                winner, outcome = solve_outcome(players)
                earliest = min((p["solved_at"] for p in players if p["solved_at"] is not None), default=None)
                earlier_pending = earliest is not None and any(s <= earliest for s in pending_stamps)
                if (complete and earliest is not None and not earlier_pending
                        and (match["options"]["end_rule"] == "first_solve"
                             or all(p["solved_at"] is not None for p in players))):
                    if confirmed:
                        await self._finish(match, "finished", outcome, winner)
                    else:
                        # Earlier queried players may have submitted while later histories
                        # were being fetched. Re-read everyone after observing a candidate.
                        confirm_candidate = True
                elif match["options"]["end_rule"] == "all_solve":
                    # Each solver is unmuted immediately; the other player may continue.
                    await self._cleanup(match)
            if confirm_candidate:
                await self.poll(guild, match_id, confirmed=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.last_poll[match_id] = {"complete": False, "pending": False}
            log.exception("Blitz solve check failed for %s", match_id)

    async def close(self, guild=None):
        jobs = [*self.clocks.values(), *self.polls.values(), *self.startups.values()]
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        if guild is not None:
            for snapshot in self.data.matches(guild):
                async with self.game_lock(snapshot["id"]):
                    match = self.data.match(guild, snapshot["id"])
                    if match["state"] not in TERMINAL:
                        await self._finish(match, "cancelled", "Bot shutting down; match cancelled and voices released.")
                    else:
                        await self._cleanup(match)

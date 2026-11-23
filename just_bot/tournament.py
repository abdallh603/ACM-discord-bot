"""Tournament coordination: normal Blitz games remain responsible for consent and voice safety."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
import uuid

from .blitz import TERMINAL, validate_options
from .domain import UserError
from .tournament_store import TournamentStore

log = logging.getLogger(__name__)


def seed_order(size):
    order = [1, 2]
    while len(order) < size:
        width = len(order) * 2 + 1
        order = [value for seed in order for value in (seed, width - seed)]
    return order


class Tournaments:
    def __init__(self, community, blitz, platform):
        self.community, self.blitz, self.platform = community, blitz, platform
        self.data = TournamentStore(community.store)
        self.locks, self.jobs = {}, {}

    def lock(self, tournament_id):
        return self.locks.setdefault(tournament_id, asyncio.Lock())

    @staticmethod
    def can_manage(tournament, actor, organizer=False):
        return organizer or actor in {tournament["creator_id"], tournament["options"].get("manager_id")}

    def managed(self, guild, tournament_id, actor, organizer=False):
        tournament = self.data.get(guild, tournament_id)
        if not self.can_manage(tournament, actor, organizer):
            raise UserError("Only this tournament's host, manager, or a server organizer can do that.")
        return tournament

    async def notify(self, tournament):
        try:
            delivered = await self.platform.update(tournament)
            if delivered is not False:
                with self.data.db:
                    self.data.db.execute("UPDATE tournaments SET needs_update=0 WHERE id=?", (tournament["id"],))
        except Exception:
            log.warning("Tournament panel update deferred for %s", tournament["id"], exc_info=True)

    async def create(self, guild, creator, name, capacity, channel, options):
        name = name.strip()
        if not name or len(name) > 80 or not 2 <= capacity <= 32:
            raise UserError("Use a name of 1–80 characters and a capacity of 2–32 players.")
        if len(self.data.active(guild)) >= 5:
            raise UserError("This server already has five open tournaments. Finish or cancel one first.")
        if options.get("manual"):
            raise UserError("Tournaments choose fresh problems each round; manual problems are not supported.")
        options = validate_options({**options, "end_rule": "first_solve", "text_channel_id": channel})
        tournament_id = uuid.uuid4().hex[:12]
        with self.data.db:
            self.data.db.execute(
                "INSERT INTO tournaments(id,guild_id,creator_id,name,state,options,capacity,channel_id,created_at) "
                "VALUES (?,?,?,?,'registration',?,?,?,?)",
                (tournament_id, guild, creator, name, json.dumps(options), capacity, channel, int(time.time())),
            )
        return self.data.get(guild, tournament_id)

    async def join(self, guild, tournament_id, user):
        async with self.lock(tournament_id):
            tournament = self.data.get(guild, tournament_id)
            if tournament["state"] != "registration":
                raise UserError("Registration is closed for this tournament.")
            entries = self.data.entries(tournament_id)
            if user in {e["user_id"] for e in entries}:
                raise UserError("You are already registered.")
            if len(entries) >= tournament["capacity"]:
                raise UserError("This tournament is full.")
            member = self.community.require_member(guild, user)
            if self.data.db.execute(
                "SELECT 1 FROM blitz_slots WHERE guild_id=? AND (user_id=? OR handle=?)",
                (guild, user, member["handle"]),
            ).fetchone():
                raise UserError("Finish your current Blitz invite/game or voice recovery before joining.")
            try:
                with self.data.db:
                    self.data.db.execute("INSERT INTO tournament_slots VALUES (?,?,?,?)",
                                         (guild, user, member["handle"], tournament_id))
                    self.data.db.execute("INSERT INTO tournament_entries VALUES (?,?,?)",
                                         (tournament_id, user, member["handle"]))
            except sqlite3.IntegrityError as exc:
                raise UserError("This player or handle is already registered in another tournament.") from exc
            await self.notify(self.data.get(guild, tournament_id))

    async def leave(self, guild, tournament_id, user):
        async with self.lock(tournament_id):
            tournament = self.data.get(guild, tournament_id)
            if tournament["state"] != "registration":
                raise UserError("The bracket has started. Ask its manager about a forfeit or cancellation.")
            with self.data.db:
                self.data.db.execute("DELETE FROM tournament_entries WHERE tournament_id=? AND user_id=?",
                                     (tournament_id, user))
                self.data.db.execute("DELETE FROM tournament_slots WHERE tournament_id=? AND user_id=?",
                                     (tournament_id, user))
            await self.notify(self.data.get(guild, tournament_id))

    async def start(self, guild, tournament_id, actor, *, organizer=False):
        async with self.lock(tournament_id):
            tournament = self.managed(guild, tournament_id, actor, organizer)
            if tournament["state"] != "registration":
                raise UserError("Only an open registration can be started.")
            entries = self.data.entries(tournament_id)
            if len(entries) < 2:
                raise UserError("At least two verified players must join first.")
            for entry in entries:
                member = self.community.require_member(guild, entry["user_id"])
                if member["handle"].casefold() != entry["handle"].casefold():
                    raise UserError("A player's handle changed; they must leave and join again before starting.")
            players = [e["user_id"] for e in entries]
            random.SystemRandom().shuffle(players)
            size = 1 << (len(players) - 1).bit_length()
            players += [None] * (size - len(players))
            self.data.insert_round(tournament_id, 1, [players[seed - 1] for seed in seed_order(size)])
            await self.notify(self.data.get(guild, tournament_id))
        await self.tick(guild)

    def complete_pair(self, pair, winner, note):
        with self.data.db:
            self.data.db.execute(
                "UPDATE tournament_pairs SET state='done',winner_id=?,note=? WHERE tournament_id=? AND round_no=? AND position=?",
                (winner, note, pair["tournament_id"], pair["round_no"], pair["position"]),
            )
            for user in (pair["player_a"], pair["player_b"]):
                if user is not None and user != winner:
                    self.data.db.execute("DELETE FROM tournament_slots WHERE tournament_id=? AND user_id=?",
                                         (pair["tournament_id"], user))

    async def resolve(self, guild, tournament_id, actor, round_no, position, winner, *, organizer=False):
        async with self.lock(tournament_id):
            tournament = self.managed(guild, tournament_id, actor, organizer)
            pair = self.data.pair(tournament_id, round_no, position)
            if tournament["state"] != "running" or pair["state"] != "blocked":
                raise UserError("Only a blocked bracket match can be resolved. Cancel a running duel first.")
            if winner not in {pair["player_a"], pair["player_b"]}:
                raise UserError("Choose one of this bracket match's two players as winner.")
            self.complete_pair(pair, winner, f"Manager decision by {actor}; original result: {pair['note'][:180]}")
            await self.notify(self.data.get(guild, tournament_id))
        await self.tick(guild)

    async def retry(self, guild, tournament_id, actor, round_no, position, *, organizer=False):
        async with self.lock(tournament_id):
            tournament = self.managed(guild, tournament_id, actor, organizer)
            pair = self.data.pair(tournament_id, round_no, position)
            if tournament["state"] != "running" or pair["state"] != "blocked":
                raise UserError("Only blocked matches can be retried.")
            self.data.update_pair(pair, state="ready", blitz_id=None, attempt=pair["attempt"] + 1, note="Retry requested.")
        await self.tick(guild)

    async def cancel(self, guild, tournament_id, actor, *, organizer=False):
        async with self.lock(tournament_id):
            tournament = self.managed(guild, tournament_id, actor, organizer)
            if tournament["state"] == "finished":
                raise UserError("This tournament has already finished.")
            self.data.update(tournament_id, state="cancelling")
            await self.cancel_games(tournament)
            await self.notify(self.data.get(guild, tournament_id))

    async def cancel_games(self, tournament):
        # This state remains retryable after a crash or failed voice operation.
        for match in self.linked_matches(tournament["guild_id"], tournament["id"]):
            await self.blitz.cancel(tournament["guild_id"], match["id"], tournament["creator_id"])
        with self.data.db:
            self.data.db.execute("DELETE FROM tournament_slots WHERE tournament_id=?", (tournament["id"],))
            self.data.db.execute("UPDATE tournaments SET state='cancelled',needs_update=1 WHERE id=?", (tournament["id"],))

    async def tick(self, guild):
        for tournament in self.data.pending(guild):
            tid = tournament["id"]
            if not self.jobs.get(tid) or self.jobs[tid].done():
                self.jobs[tid] = asyncio.create_task(self._tick_safely(guild, tid))
        await asyncio.sleep(0)

    async def _tick_safely(self, guild, tournament_id):
        try:
            await self.advance(guild, tournament_id)
        except Exception:
            log.exception("Tournament %s update failed; will retry", tournament_id)

    async def advance(self, guild, tournament_id):
        async with self.lock(tournament_id):
            tournament = self.data.get(guild, tournament_id)
            if tournament["state"] == "cancelling":
                await self.cancel_games(tournament)
                await self.notify(self.data.get(guild, tournament_id))
                return
            if tournament["state"] != "running":
                await self.notify(tournament)
                return
            pairs = self.data.pairs(tournament_id, tournament["current_round"])
            for pair in pairs:
                if pair["state"] == "ready":
                    await self.launch(tournament, pair)
                elif pair["state"] == "playing":
                    match = self.blitz.data.match(guild, pair["blitz_id"])
                    if match["state"] in TERMINAL:
                        if match["state"] == "finished" and match["winner_id"] in {pair["player_a"], pair["player_b"]}:
                            self.complete_pair(pair, match["winner_id"], "Verified Blitz result.")
                        else:
                            self.data.update_pair(pair, state="blocked", note=match["outcome"] or match["state"])
                    elif not match["options"].get("lobby_message_id") and match["state"] == "pending":
                        await self.platform.invite(tournament, match)
            pairs = self.data.pairs(tournament_id, tournament["current_round"])
            if all(p["state"] == "done" for p in pairs):
                winners = [p["winner_id"] for p in pairs]
                if len(winners) == 1:
                    with self.data.db:
                        self.data.db.execute("UPDATE tournaments SET state='finished',champion_id=?,needs_update=1 WHERE id=?",
                                             (winners[0], tournament_id))
                        self.data.db.execute("DELETE FROM tournament_slots WHERE tournament_id=?", (tournament_id,))
                else:
                    self.data.insert_round(tournament_id, tournament["current_round"] + 1, winners)
            await self.notify(self.data.get(guild, tournament_id))

    def linked_matches(self, guild, tournament_id):
        rows = self.data.db.execute("SELECT id FROM blitz_matches WHERE guild_id=? AND options LIKE ?",
                                    (guild, f'%"tournament_id": "{tournament_id}"%')).fetchall()
        matches = [self.blitz.data.match(guild, row[0]) for row in rows]
        return [m for m in matches if m["options"].get("tournament_id") == tournament_id]

    async def launch(self, tournament, pair):
        tid, guild = tournament["id"], tournament["guild_id"]
        # Repair the crash window between creating a durable Blitz and binding it to its pair.
        existing = next((m for m in self.linked_matches(guild, tid)
                         if m["options"].get("tournament_id") == tid
                         and m["options"].get("tournament_round") == pair["round_no"]
                         and m["options"].get("tournament_position") == pair["position"]
                         and m["options"].get("tournament_attempt") == pair["attempt"]), None)
        if existing is None:
            try:
                existing = await self.blitz.host(guild, tournament["creator_id"],
                    [pair["player_a"], pair["player_b"]], {**tournament["options"],
                    "tournament_id": tid, "tournament_round": pair["round_no"],
                    "tournament_position": pair["position"], "tournament_attempt": pair["attempt"]})
            except UserError as exc:
                busy = any(self.blitz.data.slot(guild, user) for user in (pair["player_a"], pair["player_b"]))
                self.data.update_pair(pair, state="ready" if busy else "blocked", note=str(exc)[:300])
                return
        self.data.update_pair(pair, state="playing", blitz_id=existing["id"], note="Players must accept the invitation.")
        if existing["state"] == "pending" and not existing["options"].get("lobby_message_id"):
            await self.platform.invite(tournament, existing)

    async def close(self):
        jobs = list(self.jobs.values())
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

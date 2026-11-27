import unittest
from unittest.mock import AsyncMock

from extensions.blitz.engine import Blitz
from just_bot.core.domain import UserError
from just_bot.core.service import Community
from just_bot.core.storage import Store
from extensions.tournament.engine import Tournaments, seed_order
from extensions.tournament.store import MIGRATIONS
from just_bot.core.schema import migrate
from extensions.blitz.tests.support import FakeVoice, blitz_store
from tests.support import problem, submission


class TournamentPlatform:
    def __init__(self, data):
        self.data = data
        self.invites, self.panels = [], []
        self.fail_update = False

    async def update(self, tournament):
        if self.fail_update:
            return False
        self.panels.append(tournament)
        return True

    async def invite(self, tournament, match):
        self.invites.append(match["id"])
        current = self.data.match(match["guild_id"], match["id"])
        self.data.update_match(match["id"], options={**current["options"], "lobby_message_id": len(self.invites)})


class TournamentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.activities = blitz_store(self.store)
        migrate(self.store.db, "tournament", MIGRATIONS)
        for guild in (10, 20):
            for user in range(1, 34):
                handle = f"player{user}"
                self.store.start_link(guild, user, handle, "nonce", 9999999999)
                self.store.complete_link(guild, user, "nonce", {"handle": handle, "rating": 1200}, 1)
        self.cf = AsyncMock()
        self.history = {}
        self.cf.submissions.side_effect = lambda handle, **kw: self.history.get(handle, [])
        pool = []
        for index in "ABCDEFG":
            item = problem()
            item["index"] = index
            pool.append(item)
        self.cf.problems.return_value = pool
        self.cf.contests.return_value = [{"id": 100, "phase": "FINISHED"}]
        self.community = Community(self.store, self.cf)
        self.voice = FakeVoice(self.activities)
        self.blitz = Blitz(self.community, self.activities, self.voice)
        self.platform = TournamentPlatform(self.activities)
        self.engine = Tournaments(self.community, self.blitz, self.platform)
        self.activities.reservation_checks.append(self.engine.check_reservation)

    async def asyncTearDown(self):
        await self.engine.close()
        await self.blitz.close()
        self.store.close()

    async def event(self, users=(1, 2, 3, 4), guild=10, **options):
        tournament = await self.engine.create(guild, 99, "Community Cup", 32, 123, options)
        for user in users:
            await self.engine.join(guild, tournament["id"], user)
        return self.engine.data.get(guild, tournament["id"])

    async def advance(self, tournament):
        await self.engine.close()  # finish/cancel scheduled ticks; run deterministic steps below
        await self.engine.advance(tournament["guild_id"], tournament["id"])

    async def start(self, tournament):
        await self.engine.start(tournament["guild_id"], tournament["id"], 99)
        await self.advance(tournament)

    async def win(self, tournament, pair):
        guild, mid = tournament["guild_id"], pair["blitz_id"]
        for player in self.activities.players(mid):
            await self.blitz.accept(guild, mid, player["user_id"])
        match = self.activities.match(guild, mid)
        winner = pair["player_a"]
        accepted = submission(match["starts_at"] + 1)
        accepted["problem"] = match["problem"]
        self.history.setdefault(f"player{winner}", []).append(accepted)
        await self.blitz.poll(guild, mid)
        self.assertEqual(self.activities.match(guild, mid)["winner_id"], winner)
        return winner

    async def test_signup_reserves_players_and_leaving_releases_them(self):
        event = await self.event((1, 2))
        with self.assertRaisesRegex(UserError, "reserved"):
            await self.blitz.challenge(10, 1, [3], {})
        other = await self.engine.create(10, 98, "Second", 8, 123, {})
        with self.assertRaisesRegex(UserError, "another tournament"):
            await self.engine.join(10, other["id"], 1)
        await self.engine.leave(10, event["id"], 1)
        await self.engine.join(10, other["id"], 1)
        self.assertEqual(len(self.engine.data.entries(other["id"])), 1)

    async def test_busy_unverified_duplicate_full_and_closed_registration_are_rejected(self):
        event = await self.engine.create(10, 99, "Tiny", 2, 123, {})
        with self.assertRaises(UserError):
            await self.engine.join(10, event["id"], 1000)
        game = await self.blitz.challenge(10, 1, [2], {})
        with self.assertRaisesRegex(UserError, "Finish your current"):
            await self.engine.join(10, event["id"], 1)
        await self.blitz.cancel(10, game["id"], 1)
        await self.engine.join(10, event["id"], 1)
        with self.assertRaisesRegex(UserError, "already registered"):
            await self.engine.join(10, event["id"], 1)
        await self.engine.join(10, event["id"], 2)
        with self.assertRaisesRegex(UserError, "full"):
            await self.engine.join(10, event["id"], 3)
        await self.start(event)
        with self.assertRaisesRegex(UserError, "closed"):
            await self.engine.join(10, event["id"], 3)
        with self.assertRaises(UserError):
            await self.engine.leave(10, event["id"], 1)

    async def test_bracket_requires_two_players_and_only_managers_can_start(self):
        event = await self.event((1,), manager_id=88)
        with self.assertRaises(UserError):
            await self.engine.start(10, event["id"], 1)
        with self.assertRaisesRegex(UserError, "At least two"):
            await self.engine.start(10, event["id"], 88)
        await self.engine.join(10, event["id"], 2)
        await self.engine.start(10, event["id"], 88)
        await self.advance(event)
        pair = self.engine.data.pairs(event["id"], 1)[0]
        self.assertEqual(pair["state"], "playing")
        players = self.activities.players(pair["blitz_id"])
        self.assertTrue(all(not p["accepted_revision"] for p in players))
        self.assertFalse(self.voice.muted)
        match = self.activities.match(10, pair["blitz_id"])
        self.assertEqual(match["options"]["manager_id"], 88)
        with self.assertRaisesRegex(UserError, "Tournament match rules"):
            await self.blitz.edit(10, match["id"], 88, {"minutes": 10})

    async def test_non_power_of_two_field_finishes_all_rounds_with_real_blitz_results(self):
        event = await self.event((1, 2, 3, 4, 5), minutes=None)
        await self.start(event)
        first = self.engine.data.pairs(event["id"], 1)
        self.assertEqual(sum(p["note"] == "Bye" for p in first), 3)
        self.assertEqual(sum(p["state"] == "playing" for p in first), 1)
        for _ in range(10):
            current = self.engine.data.get(10, event["id"])
            if current["state"] == "finished":
                break
            for pair in self.engine.data.pairs(event["id"], current["current_round"]):
                if pair["state"] == "playing":
                    await self.win(event, pair)
            await self.advance(event)
        current = self.engine.data.get(10, event["id"])
        self.assertEqual(current["state"], "finished")
        self.assertIn(current["champion_id"], {1, 2, 3, 4, 5})
        self.assertEqual(len(self.platform.invites), 4)  # N-1 actual matches, never games against BYE
        self.assertFalse(self.voice.muted)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM tournament_slots").fetchone())

    async def test_concurrent_tournaments_and_guilds_do_not_share_reservations_or_results(self):
        first = await self.event((1, 2))
        second = await self.event((3, 4))
        third = await self.event((1, 2), guild=20)
        with self.assertRaises(UserError):
            self.engine.data.get(20, first["id"])
        for event in (first, second, third):
            await self.start(event)
        await self.win(first, self.engine.data.pairs(first["id"], 1)[0])
        await self.advance(first)
        self.assertEqual(self.engine.data.get(10, first["id"])["state"], "finished")
        self.assertEqual(self.engine.data.get(10, second["id"])["state"], "running")
        self.assertEqual(self.engine.data.get(20, third["id"])["state"], "running")

    async def test_draw_is_blocked_and_manager_can_retry_or_resolve_explicitly(self):
        event = await self.event((1, 2))
        await self.start(event)
        pair = self.engine.data.pairs(event["id"], 1)[0]
        match = self.activities.match(10, pair["blitz_id"])
        await self.blitz._finish(match, "finished", "Draw", None)
        await self.advance(event)
        self.assertEqual(self.engine.data.pair(event["id"], 1, 1)["state"], "blocked")
        for actor, winner in ((1, 1), (99, 3)):
            with self.assertRaises(UserError):
                await self.engine.resolve(10, event["id"], actor, 1, 1, winner)
        await self.engine.retry(10, event["id"], 99, 1, 1)
        await self.advance(event)
        retry = self.engine.data.pair(event["id"], 1, 1)
        self.assertNotEqual(pair["blitz_id"], retry["blitz_id"])
        self.assertEqual(retry["attempt"], 2)
        await self.blitz.cancel(10, retry["blitz_id"], 1)
        await self.advance(event)
        await self.engine.resolve(10, event["id"], 99, 1, 1, 2)
        await self.advance(event)
        self.assertEqual(self.engine.data.get(10, event["id"])["champion_id"], 2)
        self.assertIn("Manager decision", self.engine.data.pair(event["id"], 1, 1)["note"])

    async def test_cancelling_event_restores_voices_and_reservations(self):
        event = await self.event((1, 2, 3, 4))
        await self.start(event)
        pair = self.engine.data.pairs(event["id"], 1)[0]
        for p in self.activities.players(pair["blitz_id"]):
            await self.blitz.accept(10, pair["blitz_id"], p["user_id"])
        self.assertTrue(self.voice.muted)
        with self.assertRaises(UserError):
            await self.engine.cancel(10, event["id"], 1)
        await self.engine.cancel(10, event["id"], 99)
        self.assertEqual(self.engine.data.get(10, event["id"])["state"], "cancelled")
        self.assertFalse(self.voice.muted)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM tournament_slots").fetchone())
        self.assertTrue(all(m["state"] == "cancelled" for m in self.engine.linked_matches(10, event["id"])))

    async def test_recovery_does_not_duplicate_games_and_repairs_unlinked_commit(self):
        event = await self.event((1, 2))
        await self.start(event)
        pair = self.engine.data.pairs(event["id"], 1)[0]
        self.engine.data.update_pair(pair, state="ready", blitz_id=None)
        await self.engine.close()
        self.engine = Tournaments(self.community, self.blitz, self.platform)
        await self.engine.advance(10, event["id"])
        self.assertEqual(self.engine.data.pair(event["id"], 1, 1)["blitz_id"], pair["blitz_id"])
        self.assertEqual(len(self.platform.invites), 1)
        self.assertEqual(len(self.engine.linked_matches(10, event["id"])), 1)

    async def test_failed_final_publication_remains_retryable_after_completion(self):
        event = await self.event((1, 2))
        await self.start(event)
        await self.win(event, self.engine.data.pairs(event["id"], 1)[0])
        self.platform.fail_update = True
        await self.advance(event)
        self.assertEqual(self.engine.data.get(10, event["id"])["state"], "finished")
        self.assertIn(event["id"], {t["id"] for t in self.engine.data.pending(10)})
        self.platform.fail_update = False
        await self.advance(event)
        self.assertNotIn(event["id"], {t["id"] for t in self.engine.data.pending(10)})

    async def test_retry_does_not_reuse_an_unsolved_problem_revealed_in_an_old_attempt(self):
        event = await self.event((1, 2))
        await self.start(event)
        first = self.engine.data.pair(event["id"], 1, 1)
        for user in (1, 2):
            await self.blitz.accept(10, first["blitz_id"], user)
        original = self.activities.match(10, first["blitz_id"])["problem"]
        alternative = {**original, "index": "H"}
        self.cf.problems.return_value = [original, alternative]
        await self.blitz.cancel(10, first["blitz_id"], 1)
        await self.advance(event)
        await self.engine.retry(10, event["id"], 99, 1, 1)
        await self.advance(event)
        second = self.engine.data.pair(event["id"], 1, 1)
        for user in (1, 2):
            await self.blitz.accept(10, second["blitz_id"], user)
        self.assertEqual(self.activities.match(10, second["blitz_id"])["problem"]["index"], "H")

    async def test_recovery_retries_interrupted_tournament_cancellation(self):
        event = await self.event((1, 2))
        await self.start(event)
        self.engine.data.update(event["id"], state="cancelling")
        await self.advance(event)
        self.assertEqual(self.engine.data.get(10, event["id"])["state"], "cancelled")
        self.assertFalse(self.store.db.execute("SELECT 1 FROM tournament_slots").fetchone())

    async def test_next_round_waits_for_disconnected_winners_voice_recovery(self):
        event = await self.event((1, 2, 3, 4))
        await self.start(event)
        first, second = self.engine.data.pairs(event["id"], 1)
        for p in self.activities.players(first["blitz_id"]):
            await self.blitz.accept(10, first["blitz_id"], p["user_id"])
        match = self.activities.match(10, first["blitz_id"])
        winner = first["player_a"]
        self.voice.disconnected.add(winner)
        accepted = submission(match["starts_at"] + 1)
        accepted["problem"] = match["problem"]
        self.history[f"player{winner}"] = [accepted]
        await self.blitz.poll(10, match["id"])
        self.assertIsNotNone(self.activities.slot(10, winner))
        await self.win(event, second)
        await self.advance(event)
        await self.advance(event)
        final = self.engine.data.pair(event["id"], 2, 1)
        self.assertEqual(final["state"], "ready")
        self.assertIsNone(final["blitz_id"])
        self.voice.disconnected.remove(winner)
        await self.blitz.voice_changed(10, winner)
        await self.advance(event)
        self.assertEqual(self.engine.data.pair(event["id"], 2, 1)["state"], "playing")

    def test_seeding_distributes_byes_and_has_no_empty_match_for_every_supported_size(self):
        for count in range(2, 33):
            size = 1 << (count - 1).bit_length()
            players = list(range(1, count + 1)) + [None] * (size - count)
            placed = [players[i - 1] for i in seed_order(size)]
            self.assertEqual(sorted(i for i in placed if i), list(range(1, count + 1)))
            self.assertTrue(all(placed[i] or placed[i + 1] for i in range(0, size, 2)))
            self.assertEqual(sum(placed[i] is None or placed[i + 1] is None for i in range(0, size, 2)), size - count)

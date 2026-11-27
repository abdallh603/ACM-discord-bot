import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from extensions.blitz.engine import Blitz, parse_problem, validate_options
from extensions.blitz.tests.support import FakeVoice, blitz_store
from just_bot.core.domain import UserError
from just_bot.core.service import Community
from just_bot.core.storage import Store
from tests.support import problem, submission


class BlitzTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.data = blitz_store(self.store)
        for user in range(1, 10):
            handle = f"player{user}"
            self.store.start_link(10, user, handle, "nonce", 9999999999)
            self.store.complete_link(10, user, "nonce", {"handle": handle, "rating": 1200}, 1)
        self.cf = AsyncMock()
        self.cf.submissions.return_value = []
        self.cf.problems.return_value = [problem()]
        self.cf.contests.return_value = [{"id": 100, "phase": "FINISHED"}]
        self.voice = FakeVoice(self.data)
        self.blitz = Blitz(Community(self.store, self.cf), self.data, self.voice)

    async def asyncTearDown(self):
        await self.blitz.close()
        self.store.close()

    async def game(self, users=(1, 2, 3), **options):
        match = await self.blitz.challenge(10, users[0], list(users[1:]), options)
        for user in users[1:]:
            match = await self.blitz.accept(10, match["id"], user)
        self.data.update_match(match["id"], starts_at=1000, ends_at=2000)
        return self.data.match(10, match["id"])

    async def test_multiplayer_requires_every_invited_player_to_accept(self):
        match = await self.blitz.challenge(10, 1, [2, 3, 4], {})
        for user in (2, 3):
            result = await self.blitz.accept(10, match["id"], user)
            self.assertEqual(result["state"], "pending")
            self.assertEqual(self.voice.muted, set())
        result = await self.blitz.accept(10, match["id"], 4)
        self.assertEqual(result["state"], "active")
        self.assertEqual(self.voice.muted, {1, 2, 3, 4})

    async def test_solo_test_starts_with_one_real_player_and_normal_voice_setup(self):
        match = await self.blitz.solo(10, 1, {"minutes": 1, "rating_min": 1200})
        self.assertEqual(match["state"], "active")
        self.assertTrue(match["options"]["solo"])
        self.assertEqual(len(self.data.players(match["id"])), 1)
        self.assertEqual(self.voice.muted, {1})
        self.assertEqual(match["ends_at"] - match["starts_at"], 60)

    async def test_solo_solve_finishes_and_releases_the_real_player(self):
        match = await self.blitz.solo(10, 1, {})
        self.cf.submissions.return_value = [submission(match["starts_at"] + 1)]
        await self.blitz.poll(10, match["id"])
        self.assertEqual(self.data.match(10, match["id"])["state"], "finished")
        self.assertFalse(self.voice.muted)
        self.assertIsNone(self.data.slot(10, 1))

    async def test_solo_timeout_releases_the_player_without_a_solve(self):
        match = await self.blitz.solo(10, 1, {"minutes": 1})
        with patch("extensions.blitz.engine.time.time", return_value=match["ends_at"] + 1):
            await self.blitz.tick(10)
        self.assertFalse(self.voice.muted)
        self.assertEqual(self.data.match(10, match["id"])["state"], "settling")

    async def test_regular_lobbies_still_require_an_opponent(self):
        with self.assertRaises(UserError):
            await self.blitz.challenge(10, 1, [], {})
        with self.assertRaises(UserError):
            self.data.create_match(10, 1, validate_options({}), [(1, "player1")])

    async def test_countdown_hides_problem_and_defers_voice_and_timer(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def countdown(match):
            self.assertEqual(match["state"], "countdown")
            self.assertIsNone(match["starts_at"])
            self.assertFalse(self.voice.muted)
            self.voice.announce_start.assert_not_awaited()
            entered.set()
            await release.wait()
        self.voice.countdown = countdown
        self.voice.announce_start = AsyncMock()
        match = await self.blitz.challenge(10, 1, [2], {"minutes": 2})
        task = asyncio.create_task(self.blitz.accept(10, match["id"], 2))
        await asyncio.wait_for(entered.wait(), 1)
        release.set()
        with patch("extensions.blitz.engine.time.time", return_value=5000):
            result = await task
        self.assertEqual((result["starts_at"], result["ends_at"]), (5000, 5120))
        self.voice.announce_start.assert_awaited_once()
        self.assertEqual(self.voice.muted, {1, 2})

    async def test_cancelling_countdown_never_reveals_or_mutes_and_does_not_block_other_game(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def countdown(match):
            if match["creator_id"] == 1:
                entered.set()
                await release.wait()
        self.voice.countdown = countdown
        self.voice.announce_start = AsyncMock()
        match = await self.blitz.challenge(10, 1, [2], {})
        task = asyncio.create_task(self.blitz.accept(10, match["id"], 2))
        await asyncio.wait_for(entered.wait(), 1)
        other = await self.game((4, 5))
        await asyncio.wait_for(self.blitz.cancel(10, match["id"], 2), 1)
        release.set()
        result = await task
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(self.voice.muted, {4, 5})
        self.assertEqual(self.voice.announce_start.await_args.args[0]["id"], other["id"])
        self.assertEqual(self.voice.announce_start.await_count, 1)

    async def test_shutdown_cancels_waiting_startup_and_frees_its_slots(self):
        entered = asyncio.Event()
        async def countdown(match):
            entered.set()
            await asyncio.Event().wait()
        self.voice.countdown = countdown
        match = await self.blitz.challenge(10, 1, [2], {})
        task = asyncio.create_task(self.blitz.accept(10, match["id"], 2))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.blitz.close(), 1)
        self.assertTrue(task.cancelled())
        self.assertEqual(self.data.match(10, match["id"])["state"], "cancelled")
        self.assertIsNone(self.data.slot(10, 1))
        self.assertFalse(self.voice.muted)

    async def test_restart_cancels_interrupted_countdown(self):
        match = await self.blitz.challenge(10, 1, [2], {})
        self.data.update_match(match["id"], state="countdown")
        await self.blitz.recover(10)
        self.assertEqual(self.data.match(10, match["id"])["state"], "cancelled")
        self.assertIsNone(self.data.slot(10, 2))

    async def test_multiple_topics_match_any_selected_topic_and_still_exclude_solves(self):
        match = await self.blitz.challenge(10, 1, [2], {"tags": ["dp", "graphs"]})
        dp, graphs, math = problem(), problem(), problem()
        dp.update(index="A", tags=["dp"])
        graphs.update(index="B", tags=["graphs"])
        math.update(index="C", tags=["math"])
        self.cf.problems.return_value = [dp, graphs, math]
        self.cf.submissions.return_value = [submission(1)]  # problem 100A already solved
        self.assertEqual((await self.blitz.select_problem(match))["index"], "B")
        with self.assertRaises(UserError):
            validate_options({"tags": ["dp"], "random_topic": True})
        with self.assertRaises(UserError):
            validate_options({"tags": ["dp"], "manual": "4A"})

    async def test_simultaneous_games_have_separate_players_rooms_and_winners(self):
        first = await self.game()
        second = await self.game((4, 5, 6))
        self.assertNotEqual(first["voice_id"], second["voice_id"])
        async def history(handle, **kwargs):
            return [submission(1100)] if handle == "player3" else []
        self.cf.submissions.side_effect = history
        await self.blitz.poll(10, first["id"])
        self.assertEqual(self.data.match(10, first["id"])["winner_id"], 3)
        self.assertEqual(self.data.match(10, second["id"])["state"], "active")
        self.assertEqual(self.voice.muted, {4, 5, 6})
        self.assertIsNotNone(self.data.slot(10, 4))

    async def test_player_cannot_overlap_games_and_failed_creation_is_atomic(self):
        await self.game()
        with self.assertRaisesRegex(UserError, "already has"):
            await self.blitz.challenge(10, 4, [2, 5], {})
        self.assertIsNone(self.data.slot(10, 4))
        self.assertEqual(len(self.data.matches(10)), 1)

    async def test_settings_edit_invalidates_previous_acceptances(self):
        match = await self.blitz.challenge(10, 1, [2, 3], {})
        await self.blitz.accept(10, match["id"], 2)
        match = await self.blitz.edit(10, match["id"], 1, {"minutes": 45})
        self.assertEqual(match["options"]["revision"], 2)
        with self.assertRaisesRegex(UserError, "Settings changed"):
            await self.blitz.accept(10, match["id"], 3)
        await self.blitz.accept(10, match["id"], 2, revision=2)
        match = await self.blitz.accept(10, match["id"], 3, revision=2)
        self.assertEqual(match["ends_at"] - match["starts_at"], 45 * 60)

    async def test_winner_is_earliest_submission_across_all_players(self):
        match = await self.game()
        async def history(handle, **kwargs):
            stamps = {"player1": 1200, "player2": 1100, "player3": 1300}
            return [submission(stamps[handle])]
        self.cf.submissions.side_effect = history
        await self.blitz.poll(10, match["id"])
        self.assertEqual(self.data.match(10, match["id"])["winner_id"], 2)
        self.assertFalse(self.voice.muted)

    async def test_all_solve_releases_each_solver_but_keeps_every_slot_until_end(self):
        match = await self.game(end_rule="all_solve")
        solved = {"player1": 1100}
        async def history(handle, **kwargs):
            return [submission(solved[handle])] if handle in solved else []
        self.cf.submissions.side_effect = history
        await self.blitz.poll(10, match["id"])
        self.assertEqual(self.voice.muted, {2, 3})
        self.assertEqual(self.data.match(10, match["id"])["state"], "active")
        self.assertTrue(all(self.data.slot(10, user) for user in (1, 2, 3)))
        solved.update(player2=1200, player3=1300)
        await self.blitz.poll(10, match["id"])
        self.assertEqual(self.data.match(10, match["id"])["state"], "finished")
        self.assertFalse(self.voice.muted)

    async def test_timeout_unmutes_while_codeforces_check_is_stuck(self):
        match = await self.game()
        blocked = asyncio.Event()
        async def stuck(*args, **kwargs):
            await blocked.wait()
            return []
        self.cf.submissions.side_effect = stuck
        self.blitz.request_check(10, match["id"])
        await asyncio.sleep(0)
        with patch("extensions.blitz.engine.time.time", return_value=2001):
            await asyncio.wait_for(self.blitz.tick(10), timeout=1)
        self.assertFalse(self.voice.muted)
        self.assertEqual(self.data.match(10, match["id"])["state"], "settling")

    async def test_unavailable_api_never_produces_a_false_timeout_result(self):
        match = await self.game()
        self.cf.submissions.side_effect = UserError("API down")
        with self.assertLogs("extensions.blitz.engine", level="WARNING"):
            await self.blitz.poll(10, match["id"])
        with patch("extensions.blitz.engine.time.time", return_value=2121):
            await self.blitz.tick(10)
        self.assertEqual(self.data.match(10, match["id"])["state"], "unresolved")
        self.assertFalse(self.voice.muted)

    async def test_preexisting_moderator_mute_is_not_removed(self):
        self.voice.muted.add(2)
        match = await self.game()
        await self.blitz.cancel(10, match["id"], 1)
        self.assertEqual(self.voice.muted, {2})

    async def test_disconnected_player_retains_recovery_record_until_voice_rejoin(self):
        match = await self.game()
        self.voice.disconnected.add(2)
        await self.blitz.cancel(10, match["id"], 1)
        self.assertEqual(self.voice.muted, {2})
        self.assertIsNotNone(self.data.slot(10, 2))
        with self.assertRaises(UserError):
            await self.blitz.challenge(10, 4, [2], {})
        self.voice.disconnected.remove(2)
        await self.blitz.voice_changed(10, 2)
        self.assertFalse(self.voice.muted)
        self.assertIsNone(self.data.slot(10, 2))

    async def test_partial_start_failure_restores_players_already_muted(self):
        self.voice.fail_enter = 2
        match = await self.blitz.challenge(10, 1, [2, 3], {})
        await self.blitz.accept(10, match["id"], 2)
        with self.assertRaisesRegex(UserError, "permission"):
            await self.blitz.accept(10, match["id"], 3)
        self.assertFalse(self.voice.muted)
        self.assertEqual(self.data.match(10, match["id"])["state"], "cancelled")

    async def test_restart_recovers_partial_setup_without_affecting_another_game(self):
        first = await self.game()
        second = await self.game((4, 5))
        self.data.update_match(first["id"], state="starting")
        replacement = Blitz(self.blitz.community, self.data, self.voice)
        with patch("extensions.blitz.engine.time.time", return_value=1500):
            await replacement.recover(10)
        self.assertEqual(self.data.match(10, first["id"])["state"], "cancelled")
        self.assertEqual(self.data.match(10, second["id"])["state"], "active")
        self.assertEqual(self.voice.muted, {4, 5})

    async def test_manual_problem_cannot_be_from_a_running_contest(self):
        self.cf.call.return_value = {"contest": {"phase": "CODING"}, "problems": [problem()]}
        match = await self.blitz.challenge(10, 1, [2], {"manual": "100A"})
        with self.assertRaisesRegex(UserError, "finished contest"):
            await self.blitz.accept(10, match["id"], 2)

    async def test_problem_solved_by_any_one_player_is_excluded(self):
        match = await self.blitz.challenge(10, 1, [2, 3], {})
        async def history(handle, **kwargs):
            return [submission(900)] if handle == "player3" else []
        self.cf.submissions.side_effect = history
        await self.blitz.accept(10, match["id"], 2)
        with self.assertRaisesRegex(UserError, "unsolved by every"):
            await self.blitz.accept(10, match["id"], 3)

    async def test_pending_earlier_submission_delays_awarding_winner(self):
        match = await self.game()
        async def history(handle, **kwargs):
            return [submission(1100)] if handle == "player1" else [submission(1050, verdict="TESTING")]
        self.cf.submissions.side_effect = history
        await self.blitz.poll(10, match["id"])
        self.assertEqual(self.data.match(10, match["id"])["state"], "active")
        self.assertIsNone(self.data.match(10, match["id"])["winner_id"])

    async def test_one_locked_game_does_not_block_another_game_deadline(self):
        first = await self.game()
        second = await self.game((4, 5))
        lock = self.blitz.game_lock(first["id"])
        await lock.acquire()
        try:
            with patch("extensions.blitz.engine.time.time", return_value=2001):
                await asyncio.wait_for(self.blitz.tick(10), timeout=1)
            self.assertEqual(self.voice.muted, {1, 2, 3})
        finally:
            lock.release()

    async def test_selected_rating_range_and_topic_filter_the_pool(self):
        self.cf.problems.return_value = [problem(100, rating=1200, tags=["dp"]),
                                          problem(101, rating=800, tags=["dp"]),
                                          problem(102, rating=1200, tags=["greedy"])]
        self.cf.contests.return_value = [{"id": n, "phase": "FINISHED"} for n in (100, 101, 102)]
        match = await self.game(rating_min=1100, rating_max=1300, tag="dp")
        self.assertEqual(match["problem"]["contestId"], 100)

    async def test_valid_manual_problem_is_used(self):
        self.cf.call.return_value = {"contest": {"phase": "FINISHED"}, "problems": [problem()]}
        match = await self.game(manual="100A")
        self.assertEqual(match["problem"]["contestId"], 100)

    async def test_random_topic_selection_uses_an_available_topic(self):
        match = await self.game(random_topic=True)
        self.assertIn("dp", match["problem"]["tags"])

    async def test_organizer_shortening_timer_releases_voices_immediately(self):
        match = await self.game()
        with patch("extensions.blitz.engine.time.time", return_value=1200):
            updated = await self.blitz.set_time(10, match["id"], 1)
        self.assertEqual(updated["ends_at"], 1060)
        self.assertEqual(updated["state"], "settling")
        self.assertFalse(self.voice.muted)

    async def test_outsider_cannot_cancel_a_game(self):
        match = await self.game()
        with self.assertRaises(UserError):
            await self.blitz.cancel(10, match["id"], 9)
        self.assertEqual(self.data.match(10, match["id"])["state"], "active")

    async def test_equal_earliest_submission_timestamps_produce_a_draw(self):
        match = await self.game()
        self.cf.submissions.return_value = [submission(1100)]
        await self.blitz.poll(10, match["id"])
        result = self.data.match(10, match["id"])
        self.assertEqual(result["state"], "finished")
        self.assertIsNone(result["winner_id"])
        self.assertIn("Draw", result["outcome"])

    async def test_all_solve_releases_verified_solver_even_if_another_profile_is_unavailable(self):
        match = await self.game(end_rule="all_solve")
        async def history(handle, **kwargs):
            if handle == "player1":
                return [submission(1100)]
            raise UserError("API unavailable")
        self.cf.submissions.side_effect = history
        with self.assertLogs("extensions.blitz.engine", level="WARNING"):
            await self.blitz.poll(10, match["id"])
        self.assertEqual(self.voice.muted, {2, 3})
        self.assertEqual(self.data.match(10, match["id"])["state"], "active")
        self.assertFalse(self.blitz.last_poll[match["id"]]["complete"])


    async def test_omitted_time_is_unlimited_and_never_times_out(self):
        match = await self.blitz.solo(10, 1, {})
        self.assertIsNone(match["options"]["minutes"])
        self.assertIsNone(match["ends_at"])
        with patch("extensions.blitz.engine.time.time", return_value=match["starts_at"] + 864000):
            await self.blitz.tick(10)
        self.assertEqual(self.data.match(10, match["id"])["state"], "active")
        self.assertEqual(self.voice.muted, {1})

    async def test_unlimited_game_accepts_a_late_solve_and_restores_voice(self):
        match = await self.blitz.solo(10, 1, {"voice_mode": "both"})
        stamp = match["starts_at"] + 86400
        self.cf.submissions.return_value = [submission(stamp)]
        with patch("extensions.blitz.engine.time.time", return_value=stamp + 1):
            await self.blitz.poll(10, match["id"])
        self.assertEqual(self.data.match(10, match["id"])["state"], "finished")
        self.assertFalse(self.voice.muted)
        self.assertFalse(self.voice.deafened)

    async def test_timer_can_be_added_to_and_removed_from_an_unlimited_game(self):
        match = await self.blitz.solo(10, 1, {})
        timed = await self.blitz.set_time(10, match["id"], 30)
        self.assertEqual(timed["ends_at"], match["starts_at"] + 1800)
        unlimited = await self.blitz.set_time(10, match["id"], None)
        self.assertIsNone(unlimited["ends_at"])
        self.assertEqual(unlimited["state"], "active")

    async def test_editing_a_lobby_to_unlimited_still_requires_new_acceptance(self):
        match = await self.blitz.challenge(10, 1, [2], {"minutes": 3})
        updated = await self.blitz.edit(10, match["id"], 1, {"minutes": None})
        with self.assertRaises(UserError):
            await self.blitz.accept(10, match["id"], 2, revision=1)
        running = await self.blitz.accept(10, match["id"], 2, revision=updated["options"]["revision"])
        self.assertIsNone(running["ends_at"])

    async def test_deafen_only_preserves_an_existing_moderator_mute(self):
        self.voice.muted.add(1)
        match = await self.blitz.solo(10, 1, {"voice_mode": "deafen"})
        self.assertEqual(self.voice.deafened, {1})
        self.assertFalse(self.data.players(match["id"])[0]["owns_mute"])
        await self.blitz.cancel(10, match["id"], 1)
        self.assertEqual(self.voice.muted, {1})
        self.assertFalse(self.voice.deafened)

    async def test_both_mode_recovers_both_flags_after_disconnected_player_rejoins(self):
        match = await self.blitz.solo(10, 1, {"voice_mode": "both"})
        self.voice.disconnected.add(1)
        await self.blitz.cancel(10, match["id"], 1)
        self.assertIsNotNone(self.data.slot(10, 1))
        self.assertEqual((self.voice.muted, self.voice.deafened), ({1}, {1}))
        self.voice.disconnected.remove(1)
        await self.blitz.voice_changed(10, 1)
        self.assertFalse(self.voice.muted | self.voice.deafened)
        self.assertIsNone(self.data.slot(10, 1))

    async def test_candidate_winner_is_confirmed_against_previously_queried_players(self):
        match = await self.game((1, 2))
        calls = {"player1": 0, "player2": 0}
        async def racing_history(handle, **kwargs):
            calls[handle] += 1
            if handle == "player1":
                return [] if calls[handle] == 1 else [submission(1100)]
            return [submission(1200)]
        self.cf.submissions.side_effect = racing_history
        await self.blitz.poll(10, match["id"])
        result = self.data.match(10, match["id"])
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["winner_id"], 1)
        self.assertTrue(all(count >= 2 for count in calls.values()))

    async def test_recovery_reapplies_unexpired_voice_policy_but_never_mutes_the_manager(self):
        match = await self.blitz.host(10, 99, [1, 2], {"voice_mode": "both"})
        await self.blitz.accept(10, match["id"], 1)
        match = await self.blitz.accept(10, match["id"], 2)
        self.voice.muted.clear()
        self.voice.deafened.clear()
        with patch("extensions.blitz.engine.time.time", return_value=match["starts_at"] + 10):
            await self.blitz.recover(10)
        self.assertEqual(self.voice.muted, {1, 2})
        self.assertEqual(self.voice.deafened, {1, 2})

    async def test_recovery_releases_expired_games_instead_of_reapplying_voice_effects(self):
        match = await self.game((1, 2), voice_mode="both")
        with patch("extensions.blitz.engine.time.time", return_value=2001):
            await self.blitz.recover(10)
        self.assertEqual(self.data.match(10, match["id"])["state"], "settling")
        self.assertFalse(self.voice.muted | self.voice.deafened)

    async def test_partial_all_solve_game_uses_complete_post_deadline_results(self):
        match = await self.game((1, 2), end_rule="all_solve")
        self.data.update_match(match["id"], state="settling")
        async def history(handle, **kwargs):
            return [submission(1100)] if handle == "player1" else []
        self.cf.submissions.side_effect = history
        for stamp in (2001, 2100):
            with patch("extensions.blitz.engine.time.time", return_value=stamp):
                await self.blitz.poll(10, match["id"])
        with patch("extensions.blitz.engine.time.time", return_value=2121):
            await self.blitz.tick(10)
        result = self.data.match(10, match["id"])
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["winner_id"], 1)
        self.assertFalse(self.voice.muted)


class BlitzOptionsTests(unittest.TestCase):
    def test_manual_problem_ids_and_urls(self):
        for value in ["4A", "4/A", "https://codeforces.com/contest/4/problem/A",
                      "https://codeforces.com/problemset/problem/4/A"]:
            self.assertEqual(parse_problem(value), (4, "A"))
        with self.assertRaises(UserError):
            parse_problem("https://example.com/contest/4/problem/A")

    def test_incompatible_filters_fail_explicitly(self):
        for options in [{"manual": "4A", "tag": "dp"}, {"random_topic": True, "tag": "dp"},
                        {"rating_min": 1500, "rating_max": 1000}, {"minutes": 181},
                        {"rating_min": 1234}]:
            with self.subTest(options=options), self.assertRaises(UserError):
                validate_options(options)

    def test_voice_modes_set_independent_flags(self):
        for mode, expected in [("mute", (True, False)), ("deafen", (False, True)),
                               ("both", (True, True)), ("none", (False, False))]:
            options = validate_options({"voice_mode": mode})
            self.assertEqual((options["mute_voice"], options["deaf_voice"]), expected)
        with self.assertRaises(UserError):
            validate_options({"voice_mode": "invalid"})

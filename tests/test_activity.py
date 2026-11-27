import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from aiohttp.test_utils import TestClient, TestServer

from just_bot.activity_server import ActivityService, DiscordGateway, ActivityError, create_app
from just_bot.activity_demo import DemoActivity
from just_bot.activity_launch import launch_activity, register_entrypoint
from just_bot.core.domain import UserError
import tests.test_tournament as fixtures


class Gateway:
    def __init__(self):
        self.users = ["1", "2", "3", "99"]
        self.lookups = 0

    async def exchange(self, code):
        return "oauth-access-fixture", {"id": code}, 1800

    async def instance(self, instance_id):
        self.lookups += 1
        return {"application_id": "777", "instance_id": instance_id, "users": list(self.users),
                "location": {"guild_id": "10", "channel_id": "500"}}

    async def close(self):
        pass


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.TournamentTests.asyncSetUp(self)
        self.event = await self.engine.create(10, 99, "Private Cup", 8, 123, {})
        self.engine.platform.pictures = SimpleNamespace(cache={})
        self.visible = True
        self.voice_channel = SimpleNamespace(id=500, name="Practice room", type=discord.ChannelType.voice)
        self.voice_channel.permissions_for = lambda member: SimpleNamespace(view_channel=True)
        self.text_channel = SimpleNamespace(permissions_for=lambda member: SimpleNamespace(view_channel=self.visible))
        self.guild = SimpleNamespace(id=10, get_channel=lambda cid: self.voice_channel if cid == 500 else self.text_channel if cid == 123 else None)
        self.guild.voice_states = {uid: SimpleNamespace(channel=self.voice_channel) for uid in (1, 2, 3, 99)}
        self.guild.fetch_member = AsyncMock(side_effect=lambda uid: SimpleNamespace(
            id=uid, name=f"member{uid}", guild=self.guild, guild_permissions=SimpleNamespace(manage_guild=False)))
        self.bot = SimpleNamespace(store=self.store, tournaments=self.engine, blitz=self.blitz, activities=self.activities,
            get_guild=lambda gid: self.guild if gid == 10 else None, guild_enabled=lambda gid: gid == 10,
            settings=SimpleNamespace(client_id="777", token="bot-fixture", client_secret="secret-fixture"))
        self.gateway = Gateway()
        self.service = ActivityService(self.bot, self.gateway)
        self.client = TestClient(TestServer(create_app(self.service)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await fixtures.TournamentTests.asyncTearDown(self)

    async def login(self, user=99, instance="i-shared"):
        response = await self.client.post("/api/session", json={"code": str(user), "instance_id": instance})
        self.assertEqual(response.status, 200, await response.text())
        return (await response.json())["session_token"]

    async def action(self, token, **body):
        return await self.client.post("/api/action", json=body, headers={"Authorization": f"Bearer {token}"})

    async def snapshot(self, token, **headers):
        return await self.client.get("/api/snapshot", headers={"Authorization": f"Bearer {token}", **headers})

    async def select(self):
        host = await self.login()
        response = await self.action(host, action="select", tournament_id=self.event["id"])
        self.assertEqual(response.status, 200, await response.text())
        return host

    async def start_event(self):
        await self.engine.join(10, self.event["id"], 1)
        await self.engine.join(10, self.event["id"], 2)
        await self.engine.start(10, self.event["id"], 99)
        await self.engine.close()
        await self.engine.advance(10, self.event["id"])
        return self.engine.data.pairs(self.event["id"], 1)[0]["blitz_id"]

    async def test_config_has_only_public_fields_and_unauthed_data_is_denied(self):
        response = await self.client.get("/api/config")
        raw = await response.text()
        self.assertIn('"client_id": "777"', raw)
        self.assertNotIn("secret-fixture", raw)
        self.assertNotIn("bot-fixture", raw)
        self.assertFalse((await response.json())["preview"])
        self.assertEqual((await self.client.get("/api/snapshot")).status, 401)

    async def test_shared_selection_is_shared_only_within_that_discord_instance(self):
        await self.select()
        player = await self.login(1)
        value = await (await self.snapshot(player)).json()
        self.assertEqual(value["tournament"]["id"], self.event["id"])
        other = await self.login(1, "i-other")
        self.assertIsNone((await (await self.snapshot(other)).json())["tournament"])

    async def test_spectators_cannot_take_over_host_selection(self):
        token = await self.login(1)
        response = await self.action(token, action="select", tournament_id=self.event["id"])
        self.assertEqual(response.status, 400)
        self.assertIn("host", (await response.json())["error"])

    async def test_private_channel_visibility_is_enforced_in_snapshots(self):
        host = await self.select()
        self.visible = False
        response = await self.snapshot(host)
        value = await response.json()
        self.assertEqual(value["tournaments"], [])
        self.assertIsNone(value["tournament"])
        self.assertNotIn("Private Cup", await response.text())

    async def test_unrevealed_problem_is_omitted_until_the_game_is_active(self):
        host = await self.select()
        mid = await self.start_event()
        secret = {"contestId": 4, "index": "A", "name": "Unrevealed fixture", "rating": 800, "tags": []}
        self.activities.update_match(mid, problem=secret, state="countdown", starts_at=1000)
        response = await self.snapshot(host)
        self.assertIsNone((await response.json())["games"][mid]["problem"])
        self.assertNotIn("Unrevealed fixture", await response.text())
        self.activities.update_match(mid, state="active")
        value = await (await self.snapshot(host)).json()
        self.assertEqual(value["games"][mid]["problem"]["name"], "Unrevealed fixture")
        self.assertNotIn("options", value["games"][mid])

    async def test_ready_uses_authenticated_player_not_a_client_supplied_user_id(self):
        await self.select()
        mid = await self.start_event()
        second = await self.login(2)
        response = await self.action(second, action="ready", tournament_id=self.event["id"], match_id=mid, revision=1, user_id=1)
        self.assertEqual(response.status, 200, await response.text())
        players = {p["user_id"]: p for p in self.activities.players(mid)}
        self.assertEqual(players[2]["accepted_revision"], 1)
        self.assertEqual(players[1]["accepted_revision"], 0)
        first = await self.login(1)
        response = await self.action(first, action="ready", tournament_id=self.event["id"], match_id=mid, revision=1)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.activities.match(10, mid)["state"], "active")
        self.assertEqual(self.voice.muted, {1, 2})

    async def test_host_cannot_accept_for_another_player_and_revision_is_required(self):
        await self.select()
        mid = await self.start_event()
        host = await self.login(99)
        response = await self.action(host, action="ready", tournament_id=self.event["id"], match_id=mid, revision=1)
        self.assertEqual(response.status, 400)
        player = await self.login(1)
        response = await self.action(player, action="ready", tournament_id=self.event["id"], match_id=mid, revision=True)
        self.assertEqual(response.status, 400)
        self.assertTrue(all(p["accepted_revision"] == 0 for p in self.activities.players(mid)))

    async def test_ready_does_not_move_a_player_out_of_the_shared_activity_room(self):
        await self.select()
        mid = await self.start_event()
        self.guild.voice_states[2] = SimpleNamespace(channel=SimpleNamespace(id=501))
        token = await self.login(1)
        response = await self.action(token, action="ready", tournament_id=self.event["id"], match_id=mid, revision=1)
        self.assertEqual(response.status, 400)
        self.assertIn("same voice room", (await response.json())["error"])
        self.assertTrue(all(p["accepted_revision"] == 0 for p in self.activities.players(mid)))

    async def test_every_action_rechecks_actual_instance_membership(self):
        token = await self.select()
        self.gateway.users.remove("1")
        response = await self.client.post("/api/session", json={"code": "1", "instance_id": "i-shared"})
        self.assertEqual(response.status, 401)
        self.gateway.users.remove("99")
        response = await self.action(token, action="cancel_tournament", tournament_id=self.event["id"])
        self.assertEqual(response.status, 401)
        self.assertEqual(self.engine.data.get(10, self.event["id"])["state"], "registration")

    async def test_participants_cannot_use_manager_controls(self):
        await self.select()
        mid = await self.start_event()
        for action in ("retry_match", "resolve_match", "set_time", "cancel_match"):
            token = await self.login(1)
            response = await self.action(token, action=action, tournament_id=self.event["id"], match_id=mid,
                round_no=1, position=1, expected_match_id=mid, winner_id="1", minutes=None, expected_ends_at=None)
            self.assertEqual(response.status, 403, await response.text())
        self.assertEqual(self.activities.match(10, mid)["state"], "pending")

    async def test_retry_uses_a_fresh_attempt_and_rejects_stale_match_ids(self):
        await self.select()
        mid = await self.start_event()
        await self.blitz.cancel(10, mid, 1)
        await self.engine.advance(10, self.event["id"])
        token = await self.login(99)
        response = await self.action(token, action="retry_match", tournament_id=self.event["id"],
                                     round_no=1, position=1, expected_match_id="obsolete")
        self.assertEqual(response.status, 400)
        token = await self.login(99)
        response = await self.action(token, action="retry_match", tournament_id=self.event["id"],
                                     round_no=1, position=1, expected_match_id=mid)
        self.assertEqual(response.status, 200, await response.text())
        pair = self.engine.data.pair(self.event["id"], 1, 1)
        self.assertNotEqual(pair["blitz_id"], mid)
        self.assertTrue(all(p["accepted_revision"] == 0 for p in self.activities.players(pair["blitz_id"])))

    async def test_manual_advancement_is_explicit_and_limited_to_pair_players(self):
        await self.select()
        mid = await self.start_event()
        await self.blitz.cancel(10, mid, 1)
        await self.engine.advance(10, self.event["id"])
        token = await self.login(99)
        response = await self.action(token, action="resolve_match", tournament_id=self.event["id"],
                                     round_no=1, position=1, expected_match_id=mid, winner_id="3")
        self.assertEqual(response.status, 400)
        token = await self.login(99)
        response = await self.action(token, action="resolve_match", tournament_id=self.event["id"],
                                     round_no=1, position=1, expected_match_id=mid, winner_id="2")
        self.assertEqual(response.status, 200, await response.text())
        pair = self.engine.data.pair(self.event["id"], 1, 1)
        self.assertEqual(pair["winner_id"], 2)
        self.assertIn("Manager decision", pair["note"])

    async def test_timer_changes_support_unlimited_and_guard_concurrent_changes(self):
        await self.select()
        mid = await self.start_event()
        for user in (1, 2):
            await self.blitz.accept(10, mid, user)
        first = self.activities.match(10, mid)
        token = await self.login(99)
        response = await self.action(token, action="set_time", tournament_id=self.event["id"], match_id=mid,
                                     minutes=10, expected_ends_at=first["ends_at"])
        self.assertEqual(response.status, 200, await response.text())
        timed = self.activities.match(10, mid)
        self.assertEqual(timed["ends_at"], timed["starts_at"] + 600)
        self.assertEqual((await response.json())["games"][mid]["minutes"], 10)
        token = await self.login(99)
        response = await self.action(token, action="set_time", tournament_id=self.event["id"], match_id=mid,
                                     minutes=15, expected_ends_at=first["ends_at"])
        self.assertEqual(response.status, 400)
        self.assertEqual(self.activities.match(10, mid)["ends_at"], timed["ends_at"])
        token = await self.login(99)
        response = await self.action(token, action="set_time", tournament_id=self.event["id"], match_id=mid,
                                     minutes=None, expected_ends_at=timed["ends_at"])
        self.assertEqual(response.status, 200, await response.text())
        self.assertIsNone(self.activities.match(10, mid)["ends_at"])
        self.assertIsNone((await response.json())["games"][mid]["minutes"])

    async def test_stopping_one_match_releases_its_voice_effects(self):
        await self.select()
        mid = await self.start_event()
        for user in (1, 2):
            await self.blitz.accept(10, mid, user)
        self.assertEqual(self.voice.muted, {1, 2})
        token = await self.login(99)
        response = await self.action(token, action="cancel_match", tournament_id=self.event["id"], match_id=mid)
        self.assertEqual(response.status, 200)
        self.assertFalse(self.voice.muted)
        self.assertEqual(self.engine.data.get(10, self.event["id"])["state"], "running")
        self.assertEqual(self.engine.data.pair(self.event["id"], 1, 1)["state"], "blocked")

    async def test_manager_control_payloads_require_current_and_typed_values(self):
        await self.select()
        mid = await self.start_event()
        for payload in ({"action": "set_time", "minutes": True, "expected_ends_at": None},
                        {"action": "set_time", "minutes": 10},
                        {"action": "retry_match", "round_no": True, "position": 1, "expected_match_id": mid},
                        {"action": "resolve_match", "round_no": 1, "position": 1, "winner_id": "1"}):
            token = await self.login(99)
            response = await self.action(token, tournament_id=self.event["id"], match_id=mid, **payload)
            self.assertEqual(response.status, 400, await response.text())

    async def test_session_expiry_and_cross_origin_posts_are_rejected(self):
        token = await self.login()
        self.service.sessions[token].expires = time.monotonic() - 1
        self.assertEqual((await self.snapshot(token)).status, 401)
        response = await self.client.post("/api/session", json={"code": "1", "instance_id": "i-shared"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)

    async def test_revoking_voice_channel_visibility_blocks_activity_access(self):
        token = await self.select()
        self.voice_channel.permissions_for = lambda member: SimpleNamespace(view_channel=False)
        self.assertEqual((await self.snapshot(token)).status, 403)

    async def test_etag_polling_and_static_route_boundaries(self):
        token = await self.select()
        response = await self.snapshot(token)
        etag = response.headers["ETag"]
        self.assertEqual((await self.snapshot(token, **{"If-None-Match": etag})).status, 304)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        for path in ("/.env", "/data/roundforge.sqlite3", "/config.py"):
            self.assertEqual((await self.client.get(path)).status, 404)
        for path in ("/", "/app.js", "/style.css", "/model.js"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_has_retry_manual_decision_and_timer_controls(self):
        demo = DemoActivity()
        request = SimpleNamespace(headers={"Authorization": "Bearer preview-session"})
        await demo.action(request, {"action": "demo_scenario", "scenario": "blocked"})
        await demo.action(request, {"action": "retry_match", "round_no": 2, "position": 1, "expected_match_id": "semi1"})
        self.assertNotEqual(demo.value["pairs"][4]["blitz_id"], "semi1")
        await demo.action(request, {"action": "demo_scenario", "scenario": "blocked"})
        await demo.action(request, {"action": "resolve_match", "round_no": 2, "position": 1, "expected_match_id": "semi1", "winner_id": "4"})
        self.assertEqual(demo.value["pairs"][4]["winner_id"], "4")
        self.assertIn("Manager decision", demo.value["pairs"][4]["note"])
        game = demo.value["games"]["semi2"]
        await demo.action(request, {"action": "set_time", "match_id": "semi2", "minutes": None, "expected_ends_at": game["ends_at"]})
        self.assertIsNone(game["ends_at"])
        self.assertIsNone(game["minutes"])
    async def test_preview_countdown_reveals_only_after_the_deadline(self):
        demo = DemoActivity()
        request = SimpleNamespace(headers={"Authorization": "Bearer preview-session"})
        await demo.action(request, {"action": "ready", "match_id": "semi1", "revision": 1})
        value = await demo.snapshot(request)
        self.assertEqual(value["games"]["semi1"]["state"], "countdown")
        self.assertIsNone(value["games"]["semi1"]["problem"])
        demo.value["games"]["semi1"]["countdown_ends_at"] = time.time() - 1
        value = await demo.snapshot(request)
        self.assertEqual(value["games"]["semi1"]["state"], "active")
        self.assertIsNotNone(value["games"]["semi1"]["problem"])

    async def test_oauth_client_mismatch_is_rejected(self):
        gateway = DiscordGateway(SimpleNamespace(client_id="777", client_secret="fixture"))
        gateway.request = AsyncMock(side_effect=[{"access_token": "oauth-fixture", "scope": "identify"}, {"application": {"id": "other"}}])
        with self.assertRaises(ActivityError):
            await gateway.exchange("example-code")


class LaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_launcher_entry_point_is_restored_after_command_sync(self):
        bot = SimpleNamespace(application_id=777, http=SimpleNamespace(upsert_global_command=AsyncMock()))
        await register_entrypoint(bot)
        payload = bot.http.upsert_global_command.call_args.kwargs["payload"]
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["handler"], 2)
        self.assertEqual(payload["contexts"], [0])

    async def test_native_launch_from_voice_chat_and_targeted_invite_from_text(self):
        voice = Mock(spec=discord.VoiceChannel)
        voice.id, voice.name = 500, "Practice room"
        voice.permissions_for.return_value = SimpleNamespace(create_instant_invite=True)
        voice.create_invite = AsyncMock(return_value=SimpleNamespace(url="https://discord.gg/example"))
        bot = SimpleNamespace(settings=SimpleNamespace(activity_enabled=True, client_id="777"), guild_enabled=lambda gid: gid == 10)
        interaction = SimpleNamespace(guild_id=10, channel_id=500, user=SimpleNamespace(voice=SimpleNamespace(channel=voice)),
            guild=SimpleNamespace(me=object()), response=SimpleNamespace(launch_activity=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        await launch_activity(bot, interaction)
        interaction.response.launch_activity.assert_awaited_once()
        voice.create_invite.assert_not_awaited()
        interaction.channel_id = 123
        await launch_activity(bot, interaction)
        self.assertEqual(voice.create_invite.call_args.kwargs["target_application_id"], 777)
        self.assertEqual(voice.create_invite.call_args.kwargs["target_type"], discord.InviteTarget.embedded_application)
        interaction.followup.send.assert_awaited_once()

    async def test_activity_launch_requires_enabled_configuration_and_voice_membership(self):
        bot = SimpleNamespace(settings=SimpleNamespace(activity_enabled=False))
        with self.assertRaises(UserError):
            await launch_activity(bot, SimpleNamespace())
        bot.settings.activity_enabled = True
        bot.guild_enabled = lambda gid: True
        with self.assertRaises(UserError):
            await launch_activity(bot, SimpleNamespace(guild_id=10, user=SimpleNamespace(voice=None)))

    async def test_text_launch_does_not_bypass_the_users_invite_permission(self):
        voice = Mock(spec=discord.VoiceChannel)
        voice.id = 500
        voice.create_invite = AsyncMock()
        voice.permissions_for.return_value = SimpleNamespace(create_instant_invite=False)
        bot = SimpleNamespace(settings=SimpleNamespace(activity_enabled=True), guild_enabled=lambda gid: True)
        interaction = SimpleNamespace(guild_id=10, channel_id=123, user=SimpleNamespace(voice=SimpleNamespace(channel=voice)))
        with self.assertRaisesRegex(UserError, "voice channel's chat"):
            await launch_activity(bot, interaction)
        voice.create_invite.assert_not_awaited()

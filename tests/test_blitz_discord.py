import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from just_bot.activities_store import Activities
from just_bot.blitz import validate_options
from just_bot.storage import Store

HAS_DISCORD = importlib.util.find_spec("discord") is not None


@unittest.skipUnless(HAS_DISCORD, "Install requirements.txt to test the Discord voice adapter")
class VoiceAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from just_bot.blitz_discord import DiscordBlitz
        self.store = Store(":memory:")
        self.data = Activities(self.store)
        self.match = self.data.create_match(10, 1, validate_options({"move_voice": False}),
                                            [(1, "player1"), (2, "player2")])
        self.members = {}
        for user in (1, 2):
            voice = SimpleNamespace(channel=SimpleNamespace(id=123), mute=False, deaf=False)
            member = SimpleNamespace(id=user, voice=voice, edit=AsyncMock(), move_to=AsyncMock())
            async def edit(*, reason, mute=None, deafen=None, member=member):
                if mute is not None:
                    member.voice.mute = mute
                if deafen is not None:
                    member.voice.deaf = deafen
            member.edit.side_effect = edit
            self.members[user] = member
        self.guild = SimpleNamespace(fetch_member=AsyncMock(side_effect=lambda user: self.members[user]),
                                     get_channel=lambda channel: None)
        self.adapter = DiscordBlitz(SimpleNamespace(activities=self.data, get_guild=lambda guild: self.guild))

    async def asyncTearDown(self):
        self.store.close()

    def closed_match(self):
        self.data.update_match(self.match["id"], state="cancelled", closed_at=9999999999,
                               outcome="Test cancellation")
        return self.data.match(10, self.match["id"])

    async def test_real_adapter_restores_only_its_own_mute(self):
        await self.adapter.enter(self.match, self.data.players(self.match["id"])[0])
        self.assertTrue(self.members[1].voice.mute)
        await self.adapter.cleanup(self.closed_match())
        self.assertFalse(self.members[1].voice.mute)
        self.assertEqual(self.members[1].edit.await_count, 2)

    async def test_real_adapter_preserves_preexisting_server_mutes(self):
        self.members[1].voice.mute = True
        await self.adapter.enter(self.match, self.data.players(self.match["id"])[0])
        await self.adapter.cleanup(self.closed_match())
        self.assertTrue(self.members[1].voice.mute)
        self.members[1].edit.assert_not_awaited()

    async def test_real_adapter_defers_disconnected_players_until_rejoin(self):
        await self.adapter.enter(self.match, self.data.players(self.match["id"])[0])
        voice = self.members[1].voice
        self.members[1].voice = None
        await self.adapter.cleanup(self.closed_match())
        self.assertEqual(self.data.players(self.match["id"])[0]["owns_mute"], 1)
        self.members[1].voice = voice
        await self.adapter.cleanup(self.closed_match())
        self.assertEqual(self.data.players(self.match["id"])[0]["owns_mute"], 0)

    async def test_failed_discord_unmute_retains_recovery_record(self):
        import discord
        await self.adapter.enter(self.match, self.data.players(self.match["id"])[0])
        self.members[1].edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing permission")
        with self.assertLogs("just_bot.blitz_discord", level="WARNING"):
            await self.adapter.cleanup(self.closed_match())
        self.assertEqual(self.data.players(self.match["id"])[0]["owns_mute"], 1)

    async def test_existing_rooms_are_reused_without_creating_channels(self):
        text_room = SimpleNamespace(id=200)
        voice_room = SimpleNamespace(id=300)
        self.guild.create_text_channel = AsyncMock()
        self.guild.create_voice_channel = AsyncMock()
        self.adapter.room_plan = AsyncMock(return_value=(list(self.members.values()), text_room, voice_room))
        await self.adapter.create_rooms(self.match)
        match = self.data.match(10, self.match["id"])
        self.assertEqual((match["text_id"], match["voice_id"]), (200, 300))
        self.assertFalse(match["options"]["owns_text_channel"])
        self.assertFalse(match["options"]["owns_voice_channel"])
        self.guild.create_text_channel.assert_not_awaited()
        self.guild.create_voice_channel.assert_not_awaited()

    async def test_cleanup_never_deletes_reused_text_or_voice_rooms(self):
        text_room = MagicMock(id=200, send=AsyncMock(), delete=AsyncMock())
        voice_room = MagicMock(id=300, members=[], delete=AsyncMock())
        channels = {200: text_room, 300: voice_room}
        self.guild.get_channel = channels.get
        self.data.update_match(
            self.match["id"], state="cancelled", text_id=200, voice_id=300, closed_at=1,
            outcome="Test finished", options={**self.match["options"],
                                               "owns_text_channel": False, "owns_voice_channel": False},
        )
        await self.adapter.cleanup(self.data.match(10, self.match["id"]))
        text_room.delete.assert_not_awaited()
        voice_room.delete.assert_not_awaited()
        match = self.data.match(10, self.match["id"])
        self.assertIsNone(match["text_id"])
        self.assertIsNone(match["voice_id"])

    def set_mode(self, mode):
        self.data.update_match(self.match["id"], options=validate_options({"move_voice": False, "voice_mode": mode}))
        self.match = self.data.match(10, self.match["id"])

    async def test_deafen_only_uses_real_deafen_parameter_and_restores_it(self):
        self.set_mode("deafen")
        await self.adapter.enter(self.match, self.data.players(self.match["id"])[0])
        self.assertFalse(self.members[1].voice.mute)
        self.assertTrue(self.members[1].voice.deaf)
        self.assertEqual(self.members[1].edit.await_args.kwargs["deafen"], True)
        await self.adapter.cleanup(self.closed_match())
        self.assertFalse(self.members[1].voice.deaf)

    async def test_combined_mode_restores_only_the_new_deaf_when_already_muted(self):
        self.set_mode("both")
        self.members[1].voice.mute = True
        await self.adapter.enter(self.match, self.data.players(self.match["id"])[0])
        await self.adapter.cleanup(self.closed_match())
        self.assertTrue(self.members[1].voice.mute)
        self.assertFalse(self.members[1].voice.deaf)

    async def test_combined_mode_restores_only_the_new_mute_when_already_deafened(self):
        self.set_mode("both")
        self.members[1].voice.deaf = True
        await self.adapter.enter(self.match, self.data.players(self.match["id"])[0])
        await self.adapter.cleanup(self.closed_match())
        self.assertFalse(self.members[1].voice.mute)
        self.assertTrue(self.members[1].voice.deaf)

    async def test_failed_undeafen_preserves_ownership_and_slot_for_retry(self):
        import discord
        self.set_mode("deafen")
        await self.adapter.enter(self.match, self.data.players(self.match["id"])[0])
        self.members[1].edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing permission")
        with self.assertLogs("just_bot.blitz_discord", level="WARNING"):
            await self.adapter.cleanup(self.closed_match())
        self.data.free_slots(self.match["id"])
        self.assertTrue(self.data.players(self.match["id"])[0]["owns_deaf"])
        self.assertIsNotNone(self.data.slot(10, 1))

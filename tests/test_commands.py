import importlib.util
import inspect
import io
from dataclasses import replace
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

HAS_DISCORD = importlib.util.find_spec("discord") is not None


@unittest.skipUnless(HAS_DISCORD, "Install requirements.txt to test Discord command registration")
class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from just_bot.core.bot import RoundForgeBot
        from just_bot.core.config import Settings
        self.bot = RoundForgeBot(Settings("test-token", 10, Path(":memory:"), frozenset({20}), "INFO"))

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_all_commands_serialize_without_a_discord_connection(self):
        import discord
        names = {c.qualified_name for c in self.bot.tree.walk_commands()
                 if not isinstance(c, discord.app_commands.Group)}
        self.assertEqual(names, {
            "activity", "handle link", "handle verify", "handle unlink", "handle show", "daily show",
            "daily claim", "rank sync", "admin configure", "admin setup_roles", "admin refresh",
            "admin status", "admin channel", "problem", "contests", "leaderboard", "help",
            "training start", "training status", "training next", "training stop",
            "upsolve start", "upsolve check", "upsolve stop",
            "blitz create", "blitz accept", "blitz edit", "blitz status", "blitz games",
            "blitz check", "blitz cancel", "blitz time", "blitz recover", "blitz solo", "blitz menu", "blitz host", "blitz standings",
            "tournament create", "tournament join", "tournament leave", "tournament start", "tournament bracket",
            "tournament list", "tournament retry", "tournament resolve", "tournament cancel",
        })
        for command in self.bot.tree.get_commands():
            payload = command.to_dict(self.bot.tree)
            self.assertLessEqual(len(payload["description"]), 100)
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.message_content)
        self.assertTrue(self.bot.intents.voice_states)

    async def test_every_admin_command_enforces_permissions_at_runtime(self):
        import discord
        group = self.bot.tree.get_command("admin")
        interaction = SimpleNamespace(
            permissions=discord.Permissions.none(), app_permissions=discord.Permissions.all(),
            guild_id=10, created_at=datetime.now(timezone.utc), user=SimpleNamespace(id=1),
        )
        blitz = self.bot.tree.get_command("blitz")
        restricted = [*group.commands, blitz.get_command("recover"),
                      blitz.get_command("solo"), self.bot.tree.get_command("tournament").get_command("create")]
        for command in restricted:
            self.assertTrue(command.checks)
            with self.assertRaises(discord.app_commands.MissingPermissions):
                for check in command.checks:
                    result = check(interaction)
                    if inspect.isawaitable(result):
                        await result

    async def test_profile_command_attaches_card_and_keeps_text_when_files_are_not_allowed(self):
        import discord
        profile = {"handle": "player1", "rating": 1400}
        self.bot.store.start_link(10, 1, "player1", "nonce", 9999999999)
        self.bot.store.complete_link(10, 1, "nonce", profile, 1)
        self.bot.cf.users = AsyncMock(return_value=[profile])
        member = SimpleNamespace(id=1, name="discord_user", display_name="Discord User")
        interaction = SimpleNamespace(guild_id=10, user=member,
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
            app_permissions=SimpleNamespace(attach_files=True))
        group = self.bot.tree.get_command("handle")
        command = group.get_command("show")
        card = discord.File(io.BytesIO(b"png fixture"), filename="roundforge-profile.png")
        self.bot.profile_cards.build = AsyncMock(return_value=card)
        await command.callback(group, interaction, None)
        self.assertEqual(interaction.followup.send.call_args.kwargs["file"], card)
        self.assertEqual(interaction.followup.send.call_args.kwargs["embed"].image.url, "attachment://roundforge-profile.png")
        card.close()
        interaction.app_permissions.attach_files = False
        self.bot.profile_cards.build.reset_mock()
        await command.callback(group, interaction, None)
        self.bot.profile_cards.build.assert_not_awaited()
        self.assertNotIn("file", interaction.followup.send.call_args.kwargs)

    async def test_activity_checks_sdk_before_syncing_discord_commands(self):
        self.bot.settings = replace(self.bot.settings, activity_enabled=True, client_id="777", client_secret="fixture")
        self.bot.tree.sync = AsyncMock()
        with patch("just_bot.activity_setup.sdk_status", return_value=(False, "Build SDK first")):
            with self.assertRaisesRegex(ValueError, "Build SDK first"):
                await self.bot.setup_hook()
        self.bot.tree.sync.assert_not_awaited()

    async def test_other_servers_and_non_bot_channels_are_rejected(self):
        for guild_id, channel_id in [(11, 20), (10, 21)]:
            response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
            interaction = SimpleNamespace(guild_id=guild_id, channel_id=channel_id,
                                          response=response, permissions=SimpleNamespace(manage_guild=False),
                                          user=SimpleNamespace(id=1))
            self.assertFalse(await self.bot.tree.interaction_check(interaction))
            response.send_message.assert_awaited_once()

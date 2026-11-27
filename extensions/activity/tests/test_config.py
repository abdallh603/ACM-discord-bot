import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord.ext import tasks

from extensions.activity.config import ActivitySettings
from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings
from just_bot.core.extensions import ExtensionError

ENABLED = {"ACTIVITY_ENABLED": "true", "DISCORD_CLIENT_ID": "777", "DISCORD_CLIENT_SECRET": "fixture"}


def bot(env):
    return RoundForgeBot(Settings("test-token", 10, Path(":memory:"), frozenset({20}), "INFO", env=env))


class ActivitySettingsTests(unittest.TestCase):
    def test_activity_requires_separate_oauth_credentials_and_hides_secrets(self):
        with self.assertRaisesRegex(ValueError, "DISCORD_CLIENT_ID"):
            ActivitySettings.from_env({"ACTIVITY_ENABLED": "true"}, token="bot-fixture")
        env = {"ACTIVITY_ENABLED": "true", "DISCORD_CLIENT_ID": "777", "DISCORD_CLIENT_SECRET": "oauth-secret-fixture"}
        settings = ActivitySettings.from_env(env, token="bot-fixture")
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertNotIn("oauth-secret-fixture", repr(settings))
        self.assertNotIn("bot-fixture", repr(settings))

    def test_invalid_activity_port_or_flag_is_actionable(self):
        for field, value in (("ACTIVITY_PORT", "0"), ("ACTIVITY_ENABLED", "maybe")):
            with self.assertRaises(ValueError):
                ActivitySettings.from_env({field: value})


class ActivityStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_activity_checks_sdk_before_syncing_discord_commands(self):
        self.bot = bot(ENABLED)
        self.addAsyncCleanup(self.bot.close)
        self.bot.tree.sync = AsyncMock()
        with patch("extensions.activity.setup_cli.sdk_status", return_value=(False, "Build SDK first")):
            with self.assertRaisesRegex(ValueError, "Build SDK first"):
                await self.bot.setup_hook()
        self.bot.tree.sync.assert_not_awaited()

    async def test_invalid_activity_configuration_stops_startup_with_the_same_message(self):
        with self.assertRaisesRegex(ExtensionError, "^ACTIVITY_PORT must be 1–65535.$"):
            bot({"ACTIVITY_PORT": "0"})
        with self.assertRaisesRegex(ExtensionError, "^Set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET"):
            bot({"ACTIVITY_ENABLED": "true"})

    async def test_enabled_activity_adds_the_tournament_panel_button(self):
        enabled, disabled = bot(ENABLED), bot({})
        self.addAsyncCleanup(enabled.close)
        self.addAsyncCleanup(disabled.close)
        self.assertEqual([b.label for b in enabled.extensions.api("tournament").panel_buttons], ["Open voice activity"])
        self.assertEqual(disabled.extensions.api("tournament").panel_buttons, [])
        self.assertIsNotNone(disabled.tree.get_command("activity"))  # the command exists either way

    async def test_started_requires_matching_application_and_registers_entry_point(self):
        self.bot = bot(ENABLED)
        self.addAsyncCleanup(self.bot.close)
        self.bot.tree.sync = AsyncMock(return_value=[])
        self.bot._connection.application_id = 999
        with patch("extensions.activity.setup_cli.sdk_status", return_value=(True, "built")), patch.object(tasks.Loop, "start"):
            with self.assertRaisesRegex(ValueError, "DISCORD_CLIENT_ID must match"):
                await self.bot.setup_hook()
        self.bot._connection.application_id = 777
        self.bot.http.upsert_global_command = AsyncMock()
        runner = SimpleNamespace(cleanup=AsyncMock())
        with patch("extensions.activity.setup_cli.sdk_status", return_value=(True, "built")), \
                patch("extensions.activity.server.start_server", AsyncMock(return_value=runner)), patch.object(tasks.Loop, "start"):
            await self.bot.setup_hook()
        self.bot.http.upsert_global_command.assert_awaited_once()
        await self.bot.close()
        runner.cleanup.assert_awaited_once()

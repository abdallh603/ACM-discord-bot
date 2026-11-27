import os
import unittest
from unittest.mock import patch

from just_bot.core.config import Settings, parse_extensions


class ConfigTests(unittest.TestCase):
    def test_missing_token_is_actionable(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "DISCORD_TOKEN"):
            Settings.from_env()

    def test_missing_server_id_enables_public_mode(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "test"}, clear=True):
            self.assertIsNone(Settings.from_env().guild_id)

    def test_channel_allowlist_parsing(self):
        env = {"DISCORD_TOKEN": "test", "DISCORD_GUILD_ID": "10", "BOT_CHANNEL_IDS": "11, 12,"}
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.channel_ids, frozenset({11, 12}))

    def test_invalid_channel_ids_do_not_silently_disable_restrictions(self):
        env = {"DISCORD_TOKEN": "test", "DISCORD_GUILD_ID": "10", "BOT_CHANNEL_IDS": "oops"}
        with patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
            Settings.from_env()

    def test_enabled_extensions_default_none_and_explicit_lists(self):
        self.assertIsNone(parse_extensions(None))
        self.assertIsNone(parse_extensions("  "))
        self.assertEqual(parse_extensions("none"), ())
        self.assertEqual(parse_extensions(" Blitz, tournament,blitz ,"), ("blitz", "tournament"))
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            parse_extensions("none,blitz")
        env = {"DISCORD_TOKEN": "fixture", "ENABLED_EXTENSIONS": "daily,rank"}
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.extensions, ("daily", "rank"))
        self.assertEqual(settings.env["ENABLED_EXTENSIONS"], "daily,rank")
        self.assertNotIn("fixture", repr(settings))

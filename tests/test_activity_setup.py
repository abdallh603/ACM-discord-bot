import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

from just_bot.activity_setup import configure, local_checks, public_origin, sdk_status


class ActivitySetupTests(unittest.TestCase):
    def test_sdk_check_detects_missing_or_tampered_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(sdk_status(root)[0])
            raw = b"// SDK build fixture\n" * 1000
            (root / "discord-sdk.js").write_bytes(raw)
            (root / "discord-sdk-license.txt").write_text("License fixture", encoding="utf-8")
            (root / "sdk-build.json").write_text(json.dumps({"sdk_version": "2.5.0", "sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
            self.assertTrue(sdk_status(root)[0])
            (root / "discord-sdk.js").write_bytes(raw + b"changed")
            self.assertFalse(sdk_status(root)[0])
            (root / "sdk-build.json").write_text("[]", encoding="utf-8")
            self.assertFalse(sdk_status(root)[0])

    def test_status_never_includes_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            checks = local_checks({"DISCORD_TOKEN": "private-bot-fixture", "DISCORD_CLIENT_ID": "777",
                                   "DISCORD_CLIENT_SECRET": "private-oauth-fixture"}, Path(directory))
        output = json.dumps(checks)
        self.assertNotIn("private-bot-fixture", output)
        self.assertNotIn("private-oauth-fixture", output)

    def test_configuration_preserves_bot_token_and_gets_secret_without_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("DISCORD_TOKEN=bot-fixture\nDATABASE_PATH=data/example.sqlite3\n", encoding="utf-8")
            output = io.StringIO()
            with patch("just_bot.activity_setup.sdk_status", return_value=(True, "built")), patch("getpass.getpass", return_value="oauth-fixture"), redirect_stdout(output):
                configure(env, "777", 8090)
            values = dotenv_values(env)
            self.assertEqual(values["DISCORD_TOKEN"], "bot-fixture")
            self.assertEqual(values["DISCORD_CLIENT_SECRET"], "oauth-fixture")
            self.assertEqual(values["ACTIVITY_PORT"], "8090")
            self.assertNotIn("oauth-fixture", output.getvalue())

    def test_public_origin_requires_https_without_embedded_credentials(self):
        self.assertEqual(public_origin("https://activity.example/"), "https://activity.example")
        for value in ("http://activity.example", "https://name:secret@activity.example", "https://localhost", "https://example/?token=private"):
            self.assertIsNone(public_origin(value))

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from just_bot.core.images import MAX_AVATAR_BYTES, avatar_url, fetch_avatar


class AvatarFetchTests(unittest.TestCase):
    def test_url_allowlist_rejects_private_hosts_credentials_ports_and_redirect_targets(self):
        allowed = "https://userpic.codeforces.org/123/avatar/picture.jpg"
        self.assertEqual(avatar_url(allowed), allowed)
        self.assertEqual(avatar_url("//userpic.codeforces.org/123/avatar/a.png"), "https://userpic.codeforces.org/123/avatar/a.png")
        for url in ("http://userpic.codeforces.org/a.png", "https://127.0.0.1/a.png",
                    "https://userpic.codeforces.org.evil.test/a.png", "https://userpic.codeforces.org:443/a.png",
                    "https://user:password@userpic.codeforces.org/a.png", "file:///secret", None):
            self.assertIsNone(avatar_url(url))

    def test_fetch_rejects_non_images_and_limits_download_size(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Type": "text/html"}
        opener = SimpleNamespace(open=MagicMock(return_value=response))
        with patch("just_bot.core.images.build_opener", return_value=opener):
            self.assertIsNone(fetch_avatar("https://userpic.codeforces.org/a.png"))
            response.headers = {"Content-Type": "image/png"}
            response.read.return_value = b"x" * (MAX_AVATAR_BYTES + 1)
            self.assertIsNone(fetch_avatar("https://userpic.codeforces.org/a.png"))
            response.read.assert_called_once_with(MAX_AVATAR_BYTES + 1)

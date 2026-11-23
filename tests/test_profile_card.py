import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from just_bot.profile_card import MAX_AVATAR_BYTES, ProfileCards, avatar_url, fetch_avatar, render_card


def avatar(color):
    output = io.BytesIO()
    Image.new("RGB", (128, 128), color).save(output, format="PNG")
    return output.getvalue()


class ProfileCardTests(unittest.IsolatedAsyncioTestCase):
    def test_card_places_discord_avatar_on_left_and_cf_avatar_on_right(self):
        output = render_card("discord_member", "cf_handle", 1400, avatar("red"), avatar("blue"))
        with Image.open(output) as card:
            self.assertEqual(card.size, (1200, 340))
            self.assertEqual(card.getpixel((130, 180)), (255, 0, 0))
            self.assertEqual(card.getpixel((1070, 180)), (0, 0, 255))

    def test_missing_broken_oversized_and_unicode_avatars_still_render(self):
        for raw in (None, b"not an image", b"x" * (MAX_AVATAR_BYTES + 1)):
            with Image.open(render_card("عضو_المجتمع" * 10, "cf_handle", None, raw, raw)) as card:
                self.assertEqual(card.format, "PNG")

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
        with patch("just_bot.profile_card.build_opener", return_value=opener):
            self.assertIsNone(fetch_avatar("https://userpic.codeforces.org/a.png"))
            response.headers = {"Content-Type": "image/png"}
            response.read.return_value = b"x" * (MAX_AVATAR_BYTES + 1)
            self.assertIsNone(fetch_avatar("https://userpic.codeforces.org/a.png"))
            response.read.assert_called_once_with(MAX_AVATAR_BYTES + 1)

    async def test_builder_uses_real_avatar_bytes_and_returns_png_attachment(self):
        asset = SimpleNamespace(read=AsyncMock(return_value=avatar("red")))
        asset.with_size = lambda size: asset
        asset.with_static_format = lambda format: asset
        member = SimpleNamespace(name="discord_name", display_avatar=asset)
        with patch("just_bot.profile_card.fetch_avatar", return_value=avatar("blue")) as fetch:
            result = await ProfileCards().build(member, {"handle": "cf_handle", "rating": 1700, "avatar": "https://userpic.codeforces.org/a.png"})
        self.assertEqual(result.filename, "roundforge-profile.png")
        with Image.open(result.fp) as image:
            self.assertEqual(image.getpixel((1070, 180)), (0, 0, 255))
        fetch.assert_called_once()
        result.close()

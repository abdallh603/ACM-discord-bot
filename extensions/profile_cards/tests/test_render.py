import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from just_bot.core.images import MAX_AVATAR_BYTES
from extensions.profile_cards.render import ProfileCards, render_card
from tests.support import avatar


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

    async def test_builder_uses_real_avatar_bytes_and_returns_png_attachment(self):
        asset = SimpleNamespace(read=AsyncMock(return_value=avatar("red")))
        asset.with_size = lambda size: asset
        asset.with_static_format = lambda format: asset
        member = SimpleNamespace(name="discord_name", display_avatar=asset)
        with patch("extensions.profile_cards.render.fetch_avatar", return_value=avatar("blue")) as fetch:
            result = await ProfileCards().build(member, {"handle": "cf_handle", "rating": 1700, "avatar": "https://userpic.codeforces.org/a.png"})
        self.assertEqual(result.filename, "roundforge-profile.png")
        with Image.open(result.fp) as image:
            self.assertEqual(image.getpixel((1070, 180)), (0, 0, 255))
        fetch.assert_called_once()
        result.close()

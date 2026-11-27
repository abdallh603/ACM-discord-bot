"""Render an identity card from the member's real Discord and Codeforces avatars."""
from __future__ import annotations

import asyncio
import io
import logging

import discord
from PIL import Image, ImageDraw

from .core.domain import rank_for
from .core.images import draw_avatar, fetch_avatar, fitted_text, font

log = logging.getLogger(__name__)


def render_card(username, handle, rating, discord_avatar=None, cf_avatar=None):
    """This is a rendered UI card; photographs are never invented or persisted locally."""
    image = Image.new("RGB", (1200, 340), "#101722")
    draw = ImageDraw.Draw(image)
    _, color = rank_for(rating)
    accent = f"#{color:06x}"
    # Lighten dark CF ranks enough to remain visible against the card background.
    if color in {0, 0x808080}:
        accent = "#aebdd2"
    draw.rounded_rectangle((12, 12, 1187, 327), radius=24, outline="#31415a", width=2)
    draw.text((600, 48), "ROUNDFORGE  /  VERIFIED PROFILE", font=font(18), fill="#95a9c5", anchor="mm")
    draw_avatar(image, discord_avatar, (40, 90, 220, 270), str(username), "#7289ff")
    draw_avatar(image, cf_avatar, (980, 90, 1160, 270), str(handle), accent)
    label, selected = fitted_text(draw, f"{username} ~ {handle}", 710)
    draw.text((600, 162), label, font=selected, fill="#f4f6fa", anchor="mm")
    rank, _ = rank_for(rating)
    detail, small = fitted_text(draw, f"Codeforces: {rating if rating is not None else 'Unrated'} · {rank}", 710, 23)
    draw.text((600, 214), detail, font=small, fill="#f3ba63", anchor="mm")
    draw.text((130, 297), "DISCORD", font=font(15), fill="#95a9c5", anchor="mm")
    draw.text((1070, 297), "CODEFORCES", font=font(15), fill="#95a9c5", anchor="mm")
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


class ProfileCards:
    def __init__(self):
        self.slots = asyncio.Semaphore(4)

    async def build(self, member, profile):
        async def discord_picture():
            try:
                return await asyncio.wait_for(member.display_avatar.with_size(256).with_static_format("png").read(), 8)
            except (discord.HTTPException, TimeoutError, OSError):
                return None

        async def cf_picture():
            try:
                return await asyncio.to_thread(fetch_avatar, profile.get("avatar") or profile.get("titlePhoto"))
            except (OSError, ValueError):
                return None

        async with asyncio.timeout(18):
            async with self.slots:
                left, right = await asyncio.gather(discord_picture(), cf_picture())
                output = await asyncio.to_thread(render_card, member.name, profile["handle"],
                                                 profile.get("rating"), left, right)
                return discord.File(output, filename="roundforge-profile.png")

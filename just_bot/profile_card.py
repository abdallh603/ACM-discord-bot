"""Render an identity card from the member's real Discord and Codeforces avatars."""
from __future__ import annotations

import asyncio
import io
import logging
import warnings
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .domain import rank_for

log = logging.getLogger(__name__)
AVATAR_HOSTS = frozenset({"userpic.codeforces.org", "userpic.codeforces.com", "codeforces.com",
                           "cdn.codeforces.com", "cdn-1.codeforces.com", "cdn-2.codeforces.com", "cdn-3.codeforces.com"})
MAX_AVATAR_BYTES = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def avatar_url(value):
    if not isinstance(value, str):
        return None
    value = "https:" + value if value.startswith("//") else value
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.hostname not in AVATAR_HOSTS
            or parsed.username or parsed.password or parsed.netloc != parsed.hostname):
        return None
    return value


def fetch_avatar(url):
    url = avatar_url(url)
    if url is None:
        return None
    request = Request(url, headers={"User-Agent": "RoundForge/0.6", "Accept": "image/*"})
    with build_opener(NoRedirect).open(request, timeout=8) as response:
        if not response.headers.get("Content-Type", "").lower().startswith("image/"):
            return None
        value = response.read(MAX_AVATAR_BYTES + 1)
        return value if len(value) <= MAX_AVATAR_BYTES else None


def font(size):
    for path in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "C:/Windows/Fonts/segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def fitted_text(draw, text, width, maximum=36):
    text = str(text).replace("\n", " ").replace("\r", " ")[:150]
    for size in range(maximum, 15, -1):
        selected = font(size)
        if draw.textlength(text, font=selected) <= width:
            return text, selected
    while text and draw.textlength(text + "…", font=selected) > width:
        text = text[:-1]
    return text + "…", selected


def draw_avatar(canvas, raw, box, initials, accent):
    box = tuple(round(value) for value in box)
    size = box[2] - box[0]
    picture = None
    if raw and len(raw) <= MAX_AVATAR_BYTES:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as source:
                    if source.width * source.height <= 4_000_000:
                        picture = ImageOps.fit(source.convert("RGB"), (size, size))
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            pass
    if picture is None:
        picture = Image.new("RGB", (size, size), "#252e40")
        ImageDraw.Draw(picture).text((size / 2, size / 2), initials[:2].upper(), font=font(min(48, max(12, size // 3))),
                                     fill=accent, anchor="mm")
    mask = Image.new("L", (size, size))
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    canvas.paste(picture, box[:2], mask)
    ImageDraw.Draw(canvas).ellipse(box, outline=accent, width=4)


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

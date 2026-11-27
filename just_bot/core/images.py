"""Shared image helpers: fonts, fitted labels, avatar circles, and a guarded avatar download.

Only allowlisted HTTPS Codeforces hosts are fetched, redirects are refused, and both the
download size and decoded pixel count are bounded. Profile cards and tournament brackets
share this single audited implementation.
"""
from __future__ import annotations

import io
import warnings
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

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

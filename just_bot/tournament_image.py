"""Render actual bracket data into a Discord image; artwork never invents results."""
from __future__ import annotations

import asyncio
import io
import time
import warnings
from collections import OrderedDict

import discord
from PIL import Image, ImageDraw, ImageOps

from .profile_card import MAX_AVATAR_BYTES, draw_avatar, fetch_avatar, fitted_text, font

BACKGROUND = "#0c1320"
PANEL = "#172236"
TEXT = "#eef3fc"
MUTED = "#91a3be"
GREEN = "#5fe0ab"
GOLD = "#f5bc69"
BLUE = "#809eff"
WIDTH, GAP, STEP, HEIGHT = 400, 62, 180, 146


def round_name(participants):
    return {2: "FINAL", 4: "SEMI-FINALS", 8: "QUARTER-FINALS"}.get(participants, f"ROUND OF {participants}")


def bracket_nodes(pairs):
    """Include future matches and propagate known winners into the next column."""
    if not pairs:
        return []
    first = [p for p in pairs if p["round_no"] == 1]
    size = len(first) * 2
    if size < 2 or size > 32 or size & (size - 1):
        raise ValueError("Invalid tournament bracket size")
    actual = {(p["round_no"], p["position"]): p for p in pairs}
    rounds, previous = [], []
    for r in range(1, size.bit_length()):
        column = []
        for position in range(1, size // (2 ** r) + 1):
            node = dict(actual.get((r, position), {"round_no": r, "position": position,
                "player_a": None, "player_b": None, "winner_id": None, "state": "future", "note": "", "blitz_id": None}))
            if r > 1 and node["state"] == "future":
                node["player_a"] = previous[(position - 1) * 2]["winner_id"]
                node["player_b"] = previous[(position - 1) * 2 + 1]["winner_id"]
            node["y"] = 176 + ((position - .5) * 2 ** (r - 1)) * STEP
            column.append(node)
        rounds.append(column)
        previous = column
    return rounds


def status(node, games):
    if node["state"] == "done":
        if node.get("note", "").startswith("Manager decision"):
            return "MANAGER DECISION", GOLD
        return ("BYE · ADVANCED" if node.get("note") == "Bye" else "DECIDED"), GREEN
    if node["state"] == "blocked":
        return "MANAGER ACTION", GOLD
    if node["state"] == "future":
        return "UPCOMING", MUTED
    if node["state"] == "ready":
        return "WAITING", MUTED
    value = games.get(node.get("blitz_id"), "pending")
    return {"active": ("LIVE", BLUE), "countdown": ("COUNTDOWN", GOLD),
            "starting": ("PREPARING", BLUE), "settling": ("VERIFYING", GOLD),
            "pending": ("ACCEPT INVITE", BLUE)}.get(value, ("STOPPED", MUTED))


def player_row(image, x, y, user, identities, fallback, winner=False, eliminated=False, width=WIDTH - 20):
    draw = ImageDraw.Draw(image)
    outline = GREEN if winner else "#2d3d57"
    draw.rounded_rectangle((x, y, x + width, y + 48), radius=9,
                           fill="#15362f" if winner else "#101b2d", outline=outline)
    if user is None:
        draw.text((x + 16, y + 24), fallback, font=font(16), fill=MUTED, anchor="lm")
        return
    identity = identities.get(user, {"username": f"User {user}", "handle": "unavailable"})
    draw_avatar(image, identity.get("discord_avatar"), (x + 8, y + 7, x + 42, y + 41), identity["username"], "#809eff")
    draw_avatar(image, identity.get("cf_avatar"), (x + width - 42, y + 7, x + width - 8, y + 41), identity["handle"], GOLD)
    label, selected = fitted_text(draw, f"{identity['username']} ~ {identity['handle']}", width - 104, 18)
    draw.text((x + width / 2, y + 24), label, font=selected,
              fill=MUTED if eliminated else TEXT, anchor="mm")


def render_tournament(tournament, entries, pairs, identities=None, games=None):
    identities = dict(identities or {})
    games = games or {}
    for entry in entries:
        identities.setdefault(entry["user_id"], {"username": f"User {entry['user_id']}", "handle": entry["handle"]})
    rounds = bracket_nodes(pairs)
    if rounds:
        width = 80 + (len(rounds) + 1) * (WIDTH + GAP) - GAP
        height = max(530, 240 + len(rounds[0]) * STEP)
    else:
        width = 1040
        height = 260 + ((tournament["capacity"] + 1) // 2) * 72
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 5), fill=GOLD)
    draw.text((40, 33), "ROUNDFORGE  /  BLITZ TOURNAMENT", font=font(17), fill=GOLD)
    label, selected = fitted_text(draw, tournament["name"], width - 420, 34)
    draw.text((40, 64), label, font=selected, fill=TEXT)
    state = tournament["state"].upper()
    draw.text((width - 40, 46), state, font=font(18), fill=GREEN if state == "FINISHED" else BLUE, anchor="rm")
    draw.text((width - 40, 76), f"{len(entries)} players · {tournament['id']}", font=font(15), fill=MUTED, anchor="rm")
    if not rounds:
        draw.text((40, 128), f"REGISTRATION · {len(entries)}/{tournament['capacity']} joined", font=font(22), fill=TEXT)
        for slot in range(tournament["capacity"]):
            x, y = 40 + (slot % 2) * 500, 184 + (slot // 2) * 72
            user = entries[slot]["user_id"] if slot < len(entries) else None
            player_row(image, x, y, user, identities, f"Open slot {slot + 1}", width=460)
    else:
        # Draw connectors first so card backgrounds conceal the joints cleanly.
        for r, column in enumerate(rounds[:-1]):
            for index, node in enumerate(column):
                target = rounds[r + 1][index // 2]
                start, end = 40 + r * (WIDTH + GAP) + WIDTH, 40 + (r + 1) * (WIDTH + GAP)
                middle = (start + end) // 2
                color = GREEN if node["winner_id"] else "#344660"
                draw.line([(start, node["y"]), (middle, node["y"]), (middle, target["y"]), (end, target["y"])], fill=color, width=3)
        for r, column in enumerate(rounds):
            x = 40 + r * (WIDTH + GAP)
            draw.text((x, 122), round_name(len(rounds[0]) * 2 // (2 ** r)), font=font(19), fill=MUTED)
            for node in column:
                y = node["y"] - HEIGHT / 2
                draw.rounded_rectangle((x, y, x + WIDTH, y + HEIGHT), radius=13, fill=PANEL, outline="#30415c", width=2)
                caption, color = status(node, games)
                draw.text((x + 12, y + 9), f"R{r + 1} · M{node['position']}", font=font(13), fill=MUTED)
                draw.text((x + WIDTH - 12, y + 9), caption, font=font(12), fill=color, anchor="ra")
                for side, field in enumerate(("player_a", "player_b")):
                    fallback = "BYE" if node["state"] == "done" and node.get("note") == "Bye" else (
                        f"Winner R{r} · M{(node['position'] - 1) * 2 + side + 1}" if r else "Waiting for player")
                    user = node[field]
                    player_row(image, x + 10, y + 31 + side * 55, user, identities, fallback,
                               winner=user is not None and user == node["winner_id"],
                               eliminated=node["winner_id"] is not None and user != node["winner_id"])
        x, center = 40 + len(rounds) * (WIDTH + GAP), rounds[-1][0]["y"]
        draw.line([(x - GAP, center), (x, center)], fill=GOLD if tournament.get("champion_id") else "#344660", width=3)
        draw.text((x, 122), "CHAMPION", font=font(19), fill=GOLD)
        draw.rounded_rectangle((x, center - 78, x + WIDTH, center + 78), radius=15, fill="#302719", outline=GOLD, width=2)
        draw.text((x + WIDTH / 2, center - 48), "BLITZ CHAMPION", font=font(21), fill=GOLD, anchor="mm")
        player_row(image, x + 10, center - 12, tournament.get("champion_id"), identities, "Winner of the final")
        draw.text((x + WIDTH / 2, center + 55), "Confirmed winner" if tournament.get("champion_id") else "The bracket decides who advances", font=font(14), fill=MUTED, anchor="mm")
    draw.text((40, height - 34), "Discord avatar  ·  Discord username ~ Codeforces handle  ·  Codeforces avatar", font=font(15), fill=MUTED)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


def thumbnail(raw):
    if not raw or len(raw) > MAX_AVATAR_BYTES:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > 4_000_000:
                    return None
                picture = ImageOps.fit(source.convert("RGB"), (64, 64))
                out = io.BytesIO()
                picture.save(out, format="PNG")
                return out.getvalue()
    except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


class TournamentPictures:
    def __init__(self, bot):
        self.bot = bot
        self.cache = OrderedDict()
        self.downloads = asyncio.Semaphore(4)
        self.renders = asyncio.Semaphore(1)

    async def identities(self, tournament, entries):
        guild = self.bot.get_guild(tournament["guild_id"])
        result, missing = {}, []
        for entry in entries:
            key = (tournament["guild_id"], entry["user_id"], entry["handle"].casefold())
            cached = self.cache.get(key)
            result[entry["user_id"]] = dict(cached[1]) if cached else {
                "username": f"User {entry['user_id']}", "handle": entry["handle"]}
            if not cached or cached[0] < time.monotonic():
                missing.append(entry)
        if not missing:
            return result

        async def discord_identity(entry):
            async with self.downloads:
                if guild is None:
                    return
                get_member = getattr(guild, "get_member", None)
                member = get_member(entry["user_id"]) if get_member else None
                try:
                    member = member or await guild.fetch_member(entry["user_id"])
                    result[entry["user_id"]]["username"] = member.name
                    raw = await member.display_avatar.with_size(64).with_static_format("png").read()
                    result[entry["user_id"]]["discord_avatar"] = await asyncio.to_thread(thumbnail, raw)
                except (discord.HTTPException, OSError, AttributeError, TimeoutError):
                    pass

        async def codeforces_identities():
            cf = getattr(self.bot, "cf", None)
            if cf is None:
                return
            try:
                profiles = await cf.users([e["handle"] for e in missing])
            except Exception:
                return
            profiles = {p["handle"].casefold(): p for p in profiles
                        if isinstance(p, dict) and isinstance(p.get("handle"), str)} if isinstance(profiles, list) else {}
            async def picture(entry):
                profile = profiles.get(entry["handle"].casefold(), {})
                async with self.downloads:
                    try:
                        raw = await asyncio.to_thread(fetch_avatar, profile.get("avatar") or profile.get("titlePhoto"))
                        result[entry["user_id"]]["cf_avatar"] = await asyncio.to_thread(thumbnail, raw)
                    except (OSError, ValueError):
                        pass
            await asyncio.gather(*(picture(entry) for entry in missing))

        try:
            async with asyncio.timeout(5):
                await asyncio.gather(*(discord_identity(e) for e in missing), codeforces_identities())
        except TimeoutError:
            pass
        for entry in missing:
            identity = result[entry["user_id"]]
            ttl = 900 if identity.get("discord_avatar") and identity.get("cf_avatar") else 60
            key = (tournament["guild_id"], entry["user_id"], entry["handle"].casefold())
            self.cache[key] = (time.monotonic() + ttl, identity)
            self.cache.move_to_end(key)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        return result

    async def file(self, tournament, entries, pairs, games):
        identities = await self.identities(tournament, entries)
        async with self.renders:
            output = await asyncio.to_thread(render_tournament, tournament, entries, pairs, identities, games)
        return discord.File(output, filename="tournament-bracket.png")

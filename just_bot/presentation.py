from __future__ import annotations

from datetime import datetime

import discord

from zoneinfo import ZoneInfo

from .domain import BRAND, BRAND_COLOR, problem_url


def safe(value: object, limit: int = 200) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)[:limit]))


def embed(title: str, description: str = "", *, url: str | None = None):
    result = discord.Embed(title=title, description=description, color=BRAND_COLOR, url=url)
    result.set_footer(text=f"{BRAND} · Competitive programming together")
    return result


def problem_embed(problem: dict, *, daily: dict | None = None):
    title = f"{problem['contestId']}{problem['index']} · {problem['name']}"
    result = embed(title[:256], url=problem_url(problem))
    result.add_field(name="Rating", value=str(problem.get("rating", "Unrated")))
    # Spoiler tags let members attempt the problem without seeing the topic immediately.
    tags = ", ".join(problem.get("tags", []))
    result.add_field(name="Topics (spoiler)", value=f"||{safe(tags, 900)}||" if tags else "Unknown")
    if daily:
        result.description = (
            f"**RoundForge Daily · {daily['day']}**\n"
            f"Solve in practice, then use `/daily claim` for **10 points**.\n"
            f"Deadline: <t:{daily['ends_at']}:F> · <t:{daily['ends_at']}:R>\n"
            "The challenge follows your server's configured timezone."
        )
    return result


def contest_embed(contest: dict, zone=None):
    zone = zone or ZoneInfo("UTC")
    stamp = contest["startTimeSeconds"]
    local = datetime.fromtimestamp(stamp, zone).strftime("%a %d %b, %H:%M")
    return embed(
        contest["name"][:256],
        f"Starts <t:{stamp}:F> · <t:{stamp}:R>\n"
        f"**Server time:** {local} ({zone.key})\n"
        f"Duration: {contest.get('durationSeconds', 0) // 60} minutes",
        url=f"https://codeforces.com/contest/{contest['id']}",
    )

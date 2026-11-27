from __future__ import annotations

import discord

from .domain import BRAND, BRAND_COLOR, problem_url


def safe(value: object, limit: int = 200) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)[:limit]))


def embed(title: str, description: str = "", *, url: str | None = None):
    result = discord.Embed(title=title, description=description, color=BRAND_COLOR, url=url)
    result.set_footer(text=f"{BRAND} · Competitive programming together")
    return result


def problem_embed(problem: dict):
    title = f"{problem['contestId']}{problem['index']} · {problem['name']}"
    result = embed(title[:256], url=problem_url(problem))
    result.add_field(name="Rating", value=str(problem.get("rating", "Unrated")))
    # Spoiler tags let members attempt the problem without seeing the topic immediately.
    tags = ", ".join(problem.get("tags", []))
    result.add_field(name="Topics (spoiler)", value=f"||{safe(tags, 900)}||" if tags else "Unknown")
    return result

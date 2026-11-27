from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from just_bot.core.api import embed, problem_embed


def daily_embed(daily: dict):
    result = problem_embed(daily["problem"])
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

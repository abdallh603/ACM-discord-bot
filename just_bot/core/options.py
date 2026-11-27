"""Codeforces topic/rating vocabulary and native slash-command autocomplete."""
from functools import lru_cache
from zoneinfo import available_timezones

from discord import app_commands

TOPICS = (
    "2-sat", "binary search", "bitmasks", "brute force", "chinese remainder theorem",
    "combinatorics", "constructive algorithms", "data structures", "dfs and similar",
    "divide and conquer", "dp", "dsu", "expression parsing", "fft", "flows", "games",
    "geometry", "graph matchings", "graphs", "greedy", "hashing", "implementation",
    "interactive", "math", "matrices", "meet-in-the-middle", "number theory",
    "probabilities", "schedules", "shortest paths", "sortings", "string suffix structures",
    "strings", "ternary search", "trees", "two pointers",
)
RATINGS = tuple(range(800, 3501, 100))


async def topic_choices(interaction, current: str):
    value = str(current).casefold()
    return [app_commands.Choice(name=tag, value=tag) for tag in TOPICS if value in tag.casefold()][:25]


async def rating_choices(interaction, current: str):
    value = str(current)
    return [app_commands.Choice(name=str(rating), value=rating) for rating in RATINGS
            if str(rating).startswith(value)][:25]


@lru_cache(maxsize=1)
def timezones():
    common = ["UTC", "Asia/Amman", "Asia/Riyadh", "Europe/London", "America/New_York", "Asia/Kolkata"]
    return list(dict.fromkeys([*common, *sorted(available_timezones())]))


async def timezone_choices(interaction, current: str):
    value = current.casefold()
    return [app_commands.Choice(name=zone, value=zone) for zone in timezones()
            if value in zone.casefold()][:25]

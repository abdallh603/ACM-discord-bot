"""Native slash-command autocomplete; values remain usable without the setup panel."""
from functools import lru_cache
from zoneinfo import available_timezones

from discord import app_commands

from .blitz_options import RATINGS, TOPICS


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

"""Shared menu choices and voice-control settings."""
import re

from .domain import UserError

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
VOICE_LABELS = {"none": "No voice effects", "mute": "Mute", "deafen": "Deafen", "both": "Mute and deafen"}


def voice_settings(mode):
    if mode not in VOICE_LABELS:
        raise UserError("Choose none, mute, deafen, or both for voice mode.")
    return {"mute_voice": mode in {"mute", "both"}, "deaf_voice": mode in {"deafen", "both"}}


def voice_mode(options):
    mute, deaf = options.get("mute_voice", True), options.get("deaf_voice", False)
    return "both" if mute and deaf else "mute" if mute else "deafen" if deaf else "none"


def duration_label(minutes):
    return "Unlimited" if minutes is None else f"{minutes} min"


def page_items(items, page, size=25):
    pages = max(1, (len(items) + size - 1) // size)
    page %= pages
    return items[page * size:(page + 1) * size], page, pages


def parse_mentions(value, minimum=1, maximum=7, exclude=None):
    tokens = value.split()
    if not minimum <= len(tokens) <= maximum or any(not re.fullmatch(r"<@!?\d+>", token) for token in tokens):
        raise UserError(f"Mention {minimum}–{maximum} members separated by spaces.")
    users = [int(re.search(r"\d+", token)[0]) for token in tokens]
    if len(set(users)) != len(users) or exclude in users:
        raise UserError("Mention distinct players; the challenger is included automatically in normal games.")
    return users

from __future__ import annotations

import random
import re

BRAND = "RoundForge"
BRAND_COLOR = 0x137C66
RANKS = (
    (1200, "Newbie", 0x808080),
    (1400, "Pupil", 0x008000),
    (1600, "Specialist", 0x03A89E),
    (1900, "Expert", 0x0000FF),
    (2100, "Candidate Master", 0xAA00AA),
    (2300, "Master", 0xFF8C00),
    (2400, "International Master", 0xFF8C00),
    (2600, "Grandmaster", 0xFF0000),
    (3000, "International Grandmaster", 0xFF0000),
    (float("inf"), "Legendary Grandmaster", 0xFF0000),
)


class UserError(Exception):
    """A failure that can be explained directly to a Discord user."""


def validate_handle(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", value):
        raise UserError("Enter a Codeforces handle (3–24 letters, numbers, _, -, or .).")
    return value


def rank_for(rating: int | None) -> tuple[str, int]:
    if rating is None:
        return "Unrated", 0x6B7280
    for upper, name, color in RANKS:
        if rating < upper:
            return name, color
    raise AssertionError("Unreachable rating")


def problem_key(problem: dict) -> tuple[int | None, str | None]:
    return problem.get("contestId"), problem.get("index")


def problem_url(problem: dict) -> str:
    return f"https://codeforces.com/problemset/problem/{problem['contestId']}/{problem['index']}"


def choose_problem(
    problems: list[dict], rating: int, *, tag: str | None = None,
    excluded: set[tuple] | None = None, rng: random.Random | None = None,
) -> dict:
    excluded = excluded or set()
    candidates = [
        p for p in problems
        if p.get("contestId") and p["contestId"] < 100000
        and p.get("rating") == rating and p.get("type", "PROGRAMMING") == "PROGRAMMING"
        and "*special" not in p.get("tags", [])
        and (not tag or tag.casefold() in [t.casefold() for t in p.get("tags", [])])
        and problem_key(p) not in excluded
    ]
    if not candidates:
        raise UserError("No matching unsolved problem. Try another rating or tag.")
    return (rng or random).choice(candidates)


def within_window(stamp: int, start: int, end: int | None) -> bool:
    return stamp >= start and (end is None or stamp < end)


def accepted_submission(submissions: list[dict], problem: dict, start: int, end: int | None) -> dict | None:
    matches = [
        s for s in submissions
        if s.get("verdict") == "OK" and within_window(s.get("creationTimeSeconds", 0), start, end)
        and problem_key(s.get("problem", {})) == problem_key(problem)
        and s.get("author", {}).get("participantType") == "PRACTICE"
        and len(s.get("author", {}).get("members", [])) == 1
    ]
    return min(matches, key=lambda s: s["creationTimeSeconds"], default=None)

"""The stable surface for extensions. Extensions import core only from this module.

Everything else in ``just_bot.core`` is an implementation detail that may change.
"""
from .codeforces import Codeforces, CodeforcesError
from .domain import (
    BRAND,
    BRAND_COLOR,
    RANKS,
    UserError,
    accepted_submission,
    choose_problem,
    problem_key,
    problem_url,
    rank_for,
    validate_handle,
    within_window,
)
from .extensions import ExtensionContext, ExtensionError, Manifest
from .images import AVATAR_HOSTS, MAX_AVATAR_BYTES, avatar_url, draw_avatar, fetch_avatar, fitted_text, font
from .options import RATINGS, TOPICS, rating_choices, timezone_choices, timezones, topic_choices
from .presentation import embed, problem_embed, safe

__all__ = [
    "AVATAR_HOSTS", "BRAND", "BRAND_COLOR", "Codeforces", "CodeforcesError", "ExtensionContext",
    "ExtensionError", "MAX_AVATAR_BYTES", "Manifest", "RANKS", "RATINGS", "TOPICS", "UserError",
    "accepted_submission", "avatar_url", "choose_problem", "draw_avatar", "embed", "fetch_avatar",
    "fitted_text", "font", "problem_embed", "problem_key", "problem_url", "rank_for", "rating_choices",
    "safe", "timezone_choices", "timezones", "topic_choices", "validate_handle", "within_window",
]

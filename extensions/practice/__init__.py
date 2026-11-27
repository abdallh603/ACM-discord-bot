"""Practice: /training tracks and /upsolve goals."""
from __future__ import annotations

from .commands import Training, Upsolve
from .engine import Practice
from .store import MIGRATIONS, PracticeStore


def setup(ext):
    ext.migrate(MIGRATIONS)
    practice = Practice(ext.bot.community, PracticeStore(ext.db))
    ext.add_command(Training(practice), order=90)
    ext.add_command(Upsolve(practice), order=100)
    ext.help("`/training start` → `/training next` — beginner tracks", block="games", order=10)
    ext.help("`/upsolve start` → `/upsolve check` — post-contest goals", block="games", order=20)
    return practice

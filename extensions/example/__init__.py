"""A minimal RoundForge extension. Copy this folder to start your own (see ../README.md)."""
from __future__ import annotations

import logging

import discord
from discord import app_commands

log = logging.getLogger(__name__)

# Append-only: entry i upgrades schema version i to i + 1. Never edit a released entry.
MIGRATIONS = [
    """
    CREATE TABLE example_greetings (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """,
]


class Greetings:
    """This extension's storage. Its tables are prefixed with the extension name."""

    def __init__(self, db):
        self.db = db

    def greet(self, guild_id: int, user_id: int) -> int:
        with self.db:
            self.db.execute(
                "INSERT INTO example_greetings VALUES (?, ?, 1) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET count=count+1", (guild_id, user_id),
            )
        return self.db.execute("SELECT count FROM example_greetings WHERE guild_id=? AND user_id=?",
                               (guild_id, user_id)).fetchone()[0]

    def total(self) -> int:
        return self.db.execute("SELECT COALESCE(SUM(count), 0) FROM example_greetings").fetchone()[0]


def setup(ext):
    ext.migrate(MIGRATIONS)
    greetings = Greetings(ext.db)

    @app_commands.command(name="hello", description="Say hello to RoundForge (example extension)")
    async def hello(interaction: discord.Interaction):
        count = greetings.greet(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            f"Hello! You have said hello {count} time{'' if count == 1 else 's'}.", ephemeral=True)

    ext.add_command(hello)
    ext.help("`/hello` — a greeting from the example extension", block="example")

    async def report():
        log.info("Example extension: %s greetings so far", greetings.total())

    ext.add_loop(report, hours=1)

    async def joined(guild):
        log.info("Example extension: joined server %s", guild.id)

    ext.listen("guild_join", joined)
    # Whatever setup returns is this extension's public API for extensions that require it.
    return greetings

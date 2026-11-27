"""Codeforces rank roles: storage of the bot-created role IDs and member role sync."""
from __future__ import annotations

import asyncio

import discord

from just_bot.core.api import UserError, rank_for

# Append-only: entry i upgrades schema version i to i + 1.
MIGRATIONS = [
    """
CREATE TABLE rank_roles (
    guild_id INTEGER NOT NULL, rank TEXT NOT NULL, role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, rank), UNIQUE (guild_id, role_id)
);
""",
]


class RankStore:
    def __init__(self, db):
        self.db = db

    def roles(self, guild: int) -> dict[str, int]:
        return {r["rank"]: r["role_id"] for r in self.db.execute(
            "SELECT rank, role_id FROM rank_roles WHERE guild_id=?", (guild,)
        )}

    def save_role(self, guild: int, rank: str, role_id: int):
        with self.db:
            self.db.execute(
                "INSERT INTO rank_roles VALUES (?, ?, ?) ON CONFLICT(guild_id, rank) "
                "DO UPDATE SET role_id=excluded.role_id", (guild, rank, role_id),
            )


class RankRoles:
    """Grant and remove the bot's CF rank roles; only role IDs it recorded are ever touched."""

    def __init__(self, bot, store: RankStore):
        self.bot = bot
        self.store = store
        self.rank_locks = {}
        self.role_setup_locks = {}

    def rank_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self.rank_locks.setdefault((guild_id, user_id), asyncio.Lock())

    def role_setup_lock(self, guild_id):
        return self.role_setup_locks.setdefault(guild_id, asyncio.Lock())

    async def clear_rank_roles(self, member: discord.Member):
        async with self.rank_lock(member.guild.id, member.id):
            # Fetch current state; the member cache is intentionally disabled.
            member = await member.guild.fetch_member(member.id)
            managed = set(self.store.roles(member.guild.id).values())
            roles = [role for role in member.roles if role.id in managed]
            if roles:
                await member.remove_roles(*roles, reason="Codeforces account unlinked")

    async def sync_rank_role(self, member: discord.Member, rating: int | None) -> bool:
        async with self.rank_lock(member.guild.id, member.id):
            # Re-read the verified link after each REST wait, preventing stale rank grants.
            member = await member.guild.fetch_member(member.id)
            linked = self.bot.store.member(member.guild.id, member.id)
            if not linked:
                return False
            rating = linked["rating"]
            rank, _ = rank_for(rating)
            mapping = self.store.roles(member.guild.id)
            if not mapping:
                return False
            desired = member.guild.get_role(mapping.get(rank, 0))
            if not desired:
                raise UserError("A CF rank role is missing. Ask an organizer to run /admin setup_roles.")
            managed = set(mapping.values())
            # Add/remove only recorded CF roles; do not replace the user's whole role list.
            old_roles = [role for role in member.roles if role.id in managed and role.id != desired.id]
            if desired not in member.roles:
                await member.add_roles(desired, reason="Verified Codeforces rating sync")
            if old_roles:
                await member.remove_roles(*old_roles, reason="Codeforces rank changed")
            return True

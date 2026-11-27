"""Rank roles: CF rank roles synced from verified ratings (/rank sync, /admin setup_roles)."""
from __future__ import annotations

import discord

from .commands import Rank, setup_roles_command
from .roles import MIGRATIONS, RankRoles, RankStore


def setup(ext):
    ext.migrate(MIGRATIONS)
    bot = ext.bot
    roles = RankRoles(bot, RankStore(ext.db))
    ext.add_command(Rank(bot, roles), order=30)
    ext.add_admin_command(setup_roles_command(bot, roles), order=20)

    async def member_refreshed(member, linked):
        await roles.sync_rank_role(member, linked["rating"])

    async def after_unlink(member):
        try:
            await roles.clear_rank_roles(member)
        except discord.HTTPException:
            return "An organizer must remove your CF rank role because I lack permission."
        return None

    ext.hook("member_refreshed", member_refreshed)
    ext.hook("after_unlink", after_unlink, order=20)
    ext.hook("link_verified", lambda guild, user, profile: "Use `/rank sync` to update your rating role.")
    ext.hook("admin_status", lambda guild: f"Rank roles configured: **{len(roles.store.roles(guild))}**", order=50)
    ext.help("`/rank sync` — refresh your rating and CF role", block="commands", order=70)
    ext.help_item("`/admin setup_roles`", line="admin", order=30)
    return roles

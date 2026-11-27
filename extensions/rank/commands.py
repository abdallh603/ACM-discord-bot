"""/rank sync and /admin setup_roles."""
from __future__ import annotations

import time

import discord
from discord import app_commands

from just_bot.core.api import RANKS, UserError, rank_for

from .roles import RankRoles


async def defer(interaction: discord.Interaction, *, private: bool = False):
    await interaction.response.defer(ephemeral=private, thinking=True)


class Rank(app_commands.Group):
    def __init__(self, bot, roles: RankRoles):
        super().__init__(name="rank", description="Refresh your Codeforces rating and rank role")
        self.bot = bot
        self.roles = roles

    @app_commands.command(description="Refresh your linked rating and update your CF role")
    @app_commands.checks.cooldown(1, 60, key=lambda i: (i.guild_id, i.user.id))
    async def sync(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        member = self.bot.community.require_member(interaction.guild_id, interaction.user.id)
        handle = member["handle"]
        profile = (await self.bot.cf.users([handle], fresh=True))[0]
        current = self.bot.store.member(interaction.guild_id, interaction.user.id)
        if not current or current["handle"].casefold() != handle.casefold():
            raise UserError("Your account link changed. Try /rank sync again.")
        self.bot.store.update_profile(interaction.guild_id, profile, int(time.time()))
        rank, _ = rank_for(profile.get("rating"))
        changed = await self.roles.sync_rank_role(interaction.user, profile.get("rating"))
        await interaction.followup.send(
            f"Rating refreshed: **{profile.get('rating', 'Unrated')} · {rank}**. "
            + ("Rank role synced." if changed else "An organizer can enable roles with /admin setup_roles."),
            ephemeral=True,
        )


def setup_roles_command(bot, roles: RankRoles):
    @app_commands.command(name="setup_roles", description="Create the bot's CF rank roles (requires Manage Roles)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def setup_roles(interaction: discord.Interaction):
        await defer(interaction, private=True)
        async with roles.role_setup_lock(interaction.guild_id):
            mapping = roles.store.roles(interaction.guild_id)
            ranks = [("Unrated", 0x6B7280)] + [(name, color) for _, name, color in RANKS]
            created = 0
            for name, color in ranks:
                if interaction.guild.get_role(mapping.get(name, 0)):
                    continue
                role = await interaction.guild.create_role(
                    name=f"CF · {name}", color=discord.Color(color), permissions=discord.Permissions.none(),
                    reason="RoundForge bot rank role setup", hoist=True, mentionable=False,
                )
                roles.store.save_role(interaction.guild_id, name, role.id)
                created += 1
        await interaction.followup.send(
            f"Created {created} CF roles. Put my bot role above all CF roles in Server Settings → Roles. "
            "Members can use /rank sync; automatic refresh runs hourly.", ephemeral=True,
        )
    return setup_roles

"""Blitz: multiplayer Codeforces races with lobbies, countdowns, and owned voice effects."""
from __future__ import annotations

import asyncio
import logging

from just_bot.core.api import UserError

from .commands import BlitzCommands
from .discord_adapter import DiscordBlitz
from .engine import Blitz
from .store import MIGRATIONS, BlitzStore
from .views import restore_panels, send_invite

log = logging.getLogger(__name__)
HELP = (
    "`/blitz menu` → select players → choose settings → **Accept & ready** — multiplayer races",
    "`/blitz host` — manage a game for mentioned players; optional manager",
    "Select multiple topics, set time, and toggle everyone-finishes and standings in the menu.",
    "A visible 10-second countdown comes before the problem and voice effects.",
    "`/blitz standings` — verified solve times and ranks",
    "Omit blitz minutes for unlimited time; `/blitz cancel` stops the game.",
    "`/blitz games` — simultaneous games; `/blitz cancel` releases voices",
)


class BlitzRuntime:
    """Shared by Blitz's commands, views and Discord adapter; also the API for dependent extensions.

    Dependents use ``engine`` (game lifecycle), ``store`` (game records), ``send_invite``,
    ``add_reservation_check`` (refuse players inside game creation) and ``on_tick`` (run after
    each 5-second Blitz clock pass, which starts only after restart recovery).
    """

    def __init__(self, bot, store: BlitzStore):
        self.bot = bot
        self.store = store
        self.adapter = DiscordBlitz(self)
        self.engine = Blitz(bot.community, store, self.adapter)
        self.tick_listeners = []

    @property
    def community(self):
        return self.bot.community

    def get_guild(self, guild_id):
        return self.bot.get_guild(guild_id)

    def guild_enabled(self, guild_id):
        return self.bot.guild_enabled(guild_id)

    def add_view(self, view, **kwargs):
        self.bot.add_view(view, **kwargs)

    def add_reservation_check(self, check):
        self.store.reservation_checks.append(check)

    def on_tick(self, listener):
        self.tick_listeners.append(listener)

    async def send_invite(self, match, send, invited, *, followup=False):
        return await send_invite(self, match, send, invited, followup=followup)


def setup(ext):
    ext.migrate(MIGRATIONS)
    bot = ext.bot
    blitz = BlitzRuntime(bot, BlitzStore(ext.db))
    engine, store = blitz.engine, blitz.store
    ext.add_command(BlitzCommands(blitz), order=110)

    def private_game(interaction):
        # Players and managers may use commands in their game's channel outside the allowlist.
        return any(engine.can_access(match, interaction.user.id)
                   for match in store.in_channel(interaction.guild_id, interaction.channel_id))

    async def before_unlink(guild, user):
        if store.slot(guild, user):
            raise UserError("Cancel your blitz game and finish voice recovery before unlinking.")

    ext.hook("channel_exemption", private_game)
    ext.hook("before_unlink", before_unlink)

    async def voice_state_update(member, before, after):
        if not bot.guild_enabled(member.guild.id):
            return
        if before.channel != after.channel or (before.mute and not after.mute) or (before.deaf and not after.deaf):
            try:
                await engine.voice_changed(member.guild.id, member.id)
            except Exception:
                log.exception("Blitz voice update failed for %s", member.id)

    ext.listen("voice_state_update", voice_state_update)

    async def clock():
        try:
            for guild in bot.active_guilds():
                await engine.tick(guild.id)
                for listener in blitz.tick_listeners:
                    await listener(guild.id)
        except Exception:
            log.exception("Blitz clock failed; will retry")

    async def recover():
        await asyncio.gather(*(engine.recover(g.id) for g in bot.active_guilds()))

    ext.add_loop(clock, seconds=5, before=recover)

    async def restore():
        restore_panels(blitz)

    async def shutdown():
        await engine.close()
        for guild in bot.active_guilds():
            await engine.close(guild.id)

    ext.on_startup(restore)
    ext.on_shutdown(shutdown)
    for order, text in enumerate(HELP, start=30):
        ext.help(text, block="games", order=order)
    ext.help("Organizers can use `/blitz solo` to test voice controls without another account.",
             block="organizers", order=10)
    return blitz

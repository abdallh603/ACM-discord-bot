"""Tournaments: single-elimination brackets whose duels are ordinary Blitz games."""
from __future__ import annotations

from dataclasses import dataclass

import discord

from .commands import DiscordTournaments, TournamentCommands, restore_tournaments
from .engine import Tournaments
from .store import MIGRATIONS, TournamentStore


@dataclass(frozen=True)
class PanelButton:
    action: str
    label: str
    style: discord.ButtonStyle
    row: int
    handler: object  # async (interaction, panel) -> None; must answer the interaction


class TournamentRuntime:
    """Shared by tournament commands and panels; also the API for dependent extensions.

    Dependents use ``engine`` (join/start/retry/resolve/cancel/advance), ``store`` (records),
    ``pictures`` (bracket identities) and ``add_panel_button`` (extra persistent panel buttons).
    """

    def __init__(self, bot, blitz, store: TournamentStore):
        self.bot = bot
        self.blitz = blitz
        self.store = store
        self.panel_buttons: list[PanelButton] = []
        self.platform = DiscordTournaments(self)
        self.engine = Tournaments(bot.community, blitz.engine, self.platform, store)

    @property
    def cf(self):
        return self.bot.cf

    @property
    def pictures(self):
        return self.platform.pictures

    def get_guild(self, guild_id):
        return self.bot.get_guild(guild_id)

    def guild_enabled(self, guild_id):
        return self.bot.guild_enabled(guild_id)

    def add_view(self, view, **kwargs):
        self.bot.add_view(view, **kwargs)

    def add_panel_button(self, action: str, label: str, handler, *, style=discord.ButtonStyle.secondary, row: int = 1):
        """Add a persistent button to every tournament panel (custom_id rf:tournament:<guild>:<id>:<action>)."""
        self.panel_buttons.append(PanelButton(action, label, style, row, handler))


def setup(ext):
    ext.migrate(MIGRATIONS)
    blitz = ext.require("blitz")
    tournaments = TournamentRuntime(ext.bot, blitz, TournamentStore(ext.db))
    # Reserved players may play only their tournament's games; brackets advance after each Blitz tick.
    blitz.add_reservation_check(tournaments.engine.check_reservation)
    blitz.on_tick(tournaments.engine.tick)
    ext.add_command(TournamentCommands(tournaments), order=120)

    async def restore():
        restore_tournaments(tournaments)

    ext.on_startup(restore)
    ext.on_shutdown(tournaments.engine.close)
    ext.help("`/tournament create` — organizer: signup buttons, 2–32 players, automatic brackets",
             block="events", order=10)
    ext.help("`/tournament list` / `bracket` — view events; `retry` / `resolve` — manager decisions",
             block="events", order=20)
    ext.help("Each tournament duel has Accept buttons and the Blitz countdown.", block="events", order=30)
    return tournaments

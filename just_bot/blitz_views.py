"""Durable invitation buttons and game controls; slash commands remain available."""
from __future__ import annotations

import logging

import discord

from .core.domain import UserError
from .core.presentation import embed, safe

log = logging.getLogger(__name__)


def standings_embed(data, match):
    players = data.players(match["id"])
    solved = sorted((p for p in players if p["solved_at"] is not None),
                    key=lambda p: (p["solved_at"], p["user_id"]))
    rows = []
    previous, rank = None, 0
    for position, player in enumerate(solved, 1):
        if player["solved_at"] != previous:
            rank = position
        previous = player["solved_at"]
        start = match["starts_at"] if match["starts_at"] is not None else player["solved_at"]
        elapsed = max(0, player["solved_at"] - start)
        minutes, seconds = divmod(elapsed, 60)
        rows.append(f"**{rank}.** <@{player['user_id']}> · {safe(player['handle'])} · **{minutes}:{seconds:02d}**")
    for player in players:
        if player["solved_at"] is None:
            rows.append(f"— <@{player['user_id']}> · {safe(player['handle'])} · no verified solve")
    status = ("Final verified results." if match["state"] in {"finished", "timed_out"}
              else "Partial results; verification was incomplete." if match["state"] == "unresolved"
              else "Partial results; game cancelled." if match["state"] == "cancelled"
              else "Live results are provisional until verification finishes.")
    return embed(f"Blitz {match['id']} · standings", "\n".join(rows) +
                 "\n\nRanked by accepted submission time; equal timestamps share a rank. " + status)


class BlitzButton(discord.ui.Button):
    def __init__(self, panel, action, label, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, style=style,
                         custom_id=f"rf:blitz:{panel.guild_id}:{panel.match_id}:{panel.revision}:{action}")
        self.panel, self.action = panel, action

    async def callback(self, interaction):
        await self.panel.act(interaction, self.action)


class BlitzPanel(discord.ui.View):
    def __init__(self, bot, match, *, game=False, countdown=False):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id, self.match_id = match["guild_id"], match["id"]
        self.revision = match["options"]["revision"]
        if countdown:
            self.add_item(BlitzButton(self, "cancel", "Cancel blitz", discord.ButtonStyle.danger))
        elif game:
            self.add_item(BlitzButton(self, "check", "Check solves", discord.ButtonStyle.primary))
            if match["options"].get("show_standings", True):
                self.add_item(BlitzButton(self, "standings", "Standings"))
            self.add_item(BlitzButton(self, "cancel", "Cancel blitz", discord.ButtonStyle.danger))
        else:
            self.add_item(BlitzButton(self, "accept", "Accept & ready", discord.ButtonStyle.success))
            self.add_item(BlitzButton(self, "decline", "Decline", discord.ButtonStyle.danger))
        self.add_item(BlitzButton(self, "status", "Status"))

    async def act(self, interaction, action):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id != self.guild_id:
            raise UserError("This button belongs to a different server.")
        if hasattr(self.bot, "guild_enabled") and not self.bot.guild_enabled(self.guild_id):
            raise UserError("This server is not enabled in this bot's configuration.")
        match = self.bot.activities.match(self.guild_id, self.match_id)
        actor = interaction.user.id
        organizer = interaction.permissions.manage_guild
        if not self.bot.blitz.can_access(match, actor) and not organizer:
            raise UserError("This game is for its invited players and managers.")
        if action in {"accept", "decline"}:
            if match["state"] != "pending":
                raise UserError("This invitation has already started or closed. Use Status.")
            if self.revision != match["options"]["revision"]:
                raise UserError("Settings changed. Use the latest invitation buttons after reviewing the settings.")
            if actor not in {p["user_id"] for p in self.bot.activities.players(self.match_id)}:
                raise UserError("Only invited players can accept or decline; managers cannot accept for them.")
        if action == "accept":
            match = await self.bot.blitz.accept(self.guild_id, self.match_id, actor, self.revision)
        elif action in {"decline", "cancel"}:
            await self.bot.blitz.cancel(self.guild_id, self.match_id, actor, organizer=organizer)
            match = self.bot.activities.match(self.guild_id, self.match_id)
        elif action == "check":
            self.bot.blitz.request_check(self.guild_id, self.match_id)
            await interaction.followup.send("Solve check queued. The game updates automatically.", ephemeral=True)
            return
        elif action == "standings":
            if not match["options"].get("show_standings", True):
                raise UserError("Standings are disabled for this game.")
            await interaction.followup.send(embed=standings_embed(self.bot.activities, match), ephemeral=True)
            return
        from .activity_commands import blitz_embed
        await interaction.followup.send(embed=blitz_embed(self.bot, match), ephemeral=True)

    async def on_error(self, interaction, error, item):
        if not isinstance(error, UserError):
            log.error("Blitz button failed", exc_info=(type(error), error, error.__traceback__))
        message = safe(error, 1000) if isinstance(error, UserError) else "Could not update the game. Try /blitz status."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def send_invite(bot, match, send, invited, *, followup=False):
    from .activity_commands import blitz_embed
    kwargs = {"wait": True} if followup else {}
    try:
        message = await send(
            content="Invited: " + " ".join(f"<@{user}>" for user in sorted(set(invited))),
            embed=blitz_embed(bot, match), view=BlitzPanel(bot, match),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user) for user in set(invited)]),
            **kwargs,
        )
    except discord.HTTPException:
        current = bot.activities.match(match["guild_id"], match["id"])
        if current["state"] == "pending":
            await bot.blitz.cancel(match["guild_id"], match["id"], match["creator_id"])
        raise
    async with bot.blitz.game_lock(match["id"]):
        latest = bot.activities.match(match["guild_id"], match["id"])
        bot.activities.update_match(match["id"], options={**latest["options"],
            "lobby_channel_id": message.channel.id, "lobby_message_id": message.id})
        # An invitation can be accepted while its send request is completing.
        await bot.blitz.notify_lobby(bot.activities.match(match["guild_id"], match["id"]))
    return message


def restore_panels(bot):
    rows = bot.activities.db.execute(
        "SELECT guild_id,id FROM blitz_matches WHERE state IN ('pending','active','settling')"
    ).fetchall()
    for guild, mid in rows:
        match = bot.activities.match(guild, mid)
        key = "lobby_message_id" if match["state"] == "pending" else "game_message_id"
        if message_id := match["options"].get(key):
            bot.add_view(BlitzPanel(bot, match, game=match["state"] != "pending"), message_id=message_id)

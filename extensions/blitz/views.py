"""Durable invitation buttons and game controls; slash commands remain available."""
from __future__ import annotations

import logging

import discord

from just_bot.core.api import UserError, embed, safe

from .options import VOICE_LABELS, duration_label
from .options import voice_mode as selected_voice_mode

log = logging.getLogger(__name__)


def blitz_embed(blitz, match):
    options = match["options"]
    revision = options["revision"]
    players = blitz.store.players(match["id"])
    accepted = sum(p["accepted_revision"] == revision for p in players)
    roster = "\n".join(
        f"{'✅' if p['accepted_revision'] == revision else '⬜'} <@{p['user_id']}> · {safe(p['handle'])}"
        + (" · solved" if p["solved_at"] is not None else "") for p in players
    )
    filters = (f"Manual: {safe(options['manual'])}" if options["manual"] else
               f"Rating: {options['rating_min'] or options['rating_max'] or 'automatic'}–"
               f"{options['rating_max'] or options['rating_min'] or 'automatic'} · "
               f"Topics (any match): {safe(', '.join(options.get('tags', [])) or options['tag'] or ('random topic' if options['random_topic'] else 'any'))}")
    details = (
        f"Game **`{match['id']}`** · **{match['state']}** · settings revision **{revision}**\n"
        f"{duration_label(options['minutes'])} · {options['end_rule']}\n{filters}\n"
        f"Voice: **{VOICE_LABELS[selected_voice_mode(options)]}** · move to selected voice: **{options['move_voice']}**\n\n"
        f"Standings: **{'ON' if options.get('show_standings', True) else 'OFF'}**\n"
        f"Host: <@{match['creator_id']}> · Manager: <@{options.get('manager_id') or match['creator_id']}>\n"
        f"**Players ({accepted}/{len(players)} accepted)**\n{roster}\n\n"
    )
    if match["state"] == "pending":
        details += ("Click **Accept & ready** below. Everyone must accept before the countdown starts.\n"
                    f"Command alternative: `/blitz accept match_id:{match['id']} revision:{revision}`.\n"
                    f"Invite expires <t:{match['invite_expires']}:R>. Editing resets the other players' acceptance.")
    elif match["state"] == "starting":
        details += "Everyone is ready! Checking permissions and choosing a fair problem…"
    elif match["state"] == "countdown":
        details += "**Get ready!** A 10-second countdown is running in the game text channel. The problem is hidden until GO."
    elif match["starts_at"]:
        deadline = f"deadline <t:{match['ends_at']}:T>" if match["ends_at"] is not None else "unlimited time"
        details += f"Starts <t:{match['starts_at']}:T> · {deadline}.\n"
    if match["text_id"]:
        details += f"\nGame text channel: <#{match['text_id']}>"
    if match["voice_id"]:
        details += f"\nGame voice channel: <#{match['voice_id']}>"
    if match["outcome"]:
        details += f"\n{match['outcome']}"
    if match["winner_id"]:
        details += f"\nWinner: <@{match['winner_id']}>"
    recovering = [p for p in players if p["owns_mute"] or p["owns_deaf"] or p["original_voice"] is not None]
    if match["state"] in {"finished", "cancelled", "timed_out", "unresolved", "settling"} and recovering:
        details += "\nVoice recovery pending: " + ", ".join(f"<@{p['user_id']}>" for p in recovering)
    return embed("RoundForge Solo Blitz Test" if options.get("solo") else "RoundForge Multiplayer Blitz", details)


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
    def __init__(self, blitz, match, *, game=False, countdown=False):
        super().__init__(timeout=None)
        self.blitz = blitz
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
        if hasattr(self.blitz, "guild_enabled") and not self.blitz.guild_enabled(self.guild_id):
            raise UserError("This server is not enabled in this bot's configuration.")
        match = self.blitz.store.match(self.guild_id, self.match_id)
        actor = interaction.user.id
        organizer = interaction.permissions.manage_guild
        if not self.blitz.engine.can_access(match, actor) and not organizer:
            raise UserError("This game is for its invited players and managers.")
        if action in {"accept", "decline"}:
            if match["state"] != "pending":
                raise UserError("This invitation has already started or closed. Use Status.")
            if self.revision != match["options"]["revision"]:
                raise UserError("Settings changed. Use the latest invitation buttons after reviewing the settings.")
            if actor not in {p["user_id"] for p in self.blitz.store.players(self.match_id)}:
                raise UserError("Only invited players can accept or decline; managers cannot accept for them.")
        if action == "accept":
            match = await self.blitz.engine.accept(self.guild_id, self.match_id, actor, self.revision)
        elif action in {"decline", "cancel"}:
            await self.blitz.engine.cancel(self.guild_id, self.match_id, actor, organizer=organizer)
            match = self.blitz.store.match(self.guild_id, self.match_id)
        elif action == "check":
            self.blitz.engine.request_check(self.guild_id, self.match_id)
            await interaction.followup.send("Solve check queued. The game updates automatically.", ephemeral=True)
            return
        elif action == "standings":
            if not match["options"].get("show_standings", True):
                raise UserError("Standings are disabled for this game.")
            await interaction.followup.send(embed=standings_embed(self.blitz.store, match), ephemeral=True)
            return
        await interaction.followup.send(embed=blitz_embed(self.blitz, match), ephemeral=True)

    async def on_error(self, interaction, error, item):
        if not isinstance(error, UserError):
            log.error("Blitz button failed", exc_info=(type(error), error, error.__traceback__))
        message = safe(error, 1000) if isinstance(error, UserError) else "Could not update the game. Try /blitz status."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def send_invite(blitz, match, send, invited, *, followup=False):
    kwargs = {"wait": True} if followup else {}
    try:
        message = await send(
            content="Invited: " + " ".join(f"<@{user}>" for user in sorted(set(invited))),
            embed=blitz_embed(blitz, match), view=BlitzPanel(blitz, match),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user) for user in set(invited)]),
            **kwargs,
        )
    except discord.HTTPException:
        current = blitz.store.match(match["guild_id"], match["id"])
        if current["state"] == "pending":
            await blitz.engine.cancel(match["guild_id"], match["id"], match["creator_id"])
        raise
    async with blitz.engine.game_lock(match["id"]):
        latest = blitz.store.match(match["guild_id"], match["id"])
        blitz.store.update_match(match["id"], options={**latest["options"],
            "lobby_channel_id": message.channel.id, "lobby_message_id": message.id})
        # An invitation can be accepted while its send request is completing.
        await blitz.engine.notify_lobby(blitz.store.match(match["guild_id"], match["id"]))
    return message


def restore_panels(blitz):
    for guild, mid in blitz.store.restorable():
        match = blitz.store.match(guild, mid)
        key = "lobby_message_id" if match["state"] == "pending" else "game_message_id"
        if message_id := match["options"].get(key):
            blitz.add_view(BlitzPanel(blitz, match, game=match["state"] != "pending"), message_id=message_id)

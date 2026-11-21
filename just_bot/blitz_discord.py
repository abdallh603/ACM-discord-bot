from __future__ import annotations

import logging
import time

import discord

from .blitz import TERMINAL
from .blitz_rooms import choose_voice_room, owns_room
from .domain import UserError
from .presentation import embed, problem_embed

log = logging.getLogger(__name__)


class DiscordBlitz:
    def __init__(self, bot):
        self.bot = bot
        self.data = bot.activities

    def guild(self, match):
        guild = self.bot.get_guild(match["guild_id"])
        if not guild:
            raise UserError("The RoundForge Discord server is unavailable.")
        return guild

    async def preflight(self, match):
        guild = self.guild(match)
        permissions = guild.me.guild_permissions
        required = ["view_channel", "send_messages", "embed_links", "connect"]
        if match["options"]["mute_voice"]:
            required.append("mute_members")
        if match["options"].get("deaf_voice", False):
            required.append("deafen_members")
        if match["options"]["move_voice"]:
            required.append("move_members")
        missing = [name for name in required if not getattr(permissions, name)]
        if missing:
            raise UserError("Blitz needs these bot permissions: " + ", ".join(missing))
        for player in self.data.players(match["id"]):
            member = await guild.fetch_member(player["user_id"])
            if member.bot:
                raise UserError("Blitz opponents must be human members.")
            if member.voice and member.voice.channel:
                access = member.voice.channel.permissions_for(guild.me)
                if match["options"]["mute_voice"] and not access.mute_members:
                    raise UserError("I need Mute Members permission in every player's current voice channel.")
                if match["options"].get("deaf_voice", False) and not access.deafen_members:
                    raise UserError("I need Deafen Members permission in every player's current voice channel.")
                if match["options"]["move_voice"] and not access.move_members:
                    raise UserError("I need Move Members permission in every player's current voice channel.")
        _, text_room, voice_room = await self.room_plan(match)
        if (text_room is None or voice_room is None) and not permissions.manage_channels:
            raise UserError("No suitable existing room was found. Select an accessible voice room or grant Manage Channels.")

    async def room_plan(self, match):
        guild = self.guild(match)
        players = self.data.players(match["id"])
        # Prefer the challenger's current room when several players are in different rooms.
        players.sort(key=lambda p: p["user_id"] != match["creator_id"])
        members = [await guild.fetch_member(p["user_id"]) for p in players]
        managers = [await guild.fetch_member(user) for user in self.bot.blitz.managers(match)
                    if user not in {m.id for m in members}]
        text_id = match["options"].get("text_channel_id")
        text_room = guild.get_channel(text_id) if text_id else None
        if text_id:
            if not isinstance(text_room, discord.TextChannel):
                raise UserError("Start blitz from an existing server text channel available to every player.")
            if not all(text_room.permissions_for(m).view_channel for m in [*members, *managers]):
                raise UserError("Every player and manager must be able to see the game's text channel. Use a shared text channel.")
            access = text_room.permissions_for(guild.me)
            if not (access.view_channel and access.send_messages and access.embed_links):
                raise UserError("I need View Channel, Send Messages, and Embed Links in the game's text channel.")
        available = []
        ids = {m.id for m in members}
        for room in guild.voice_channels:
            if not all(room.permissions_for(m).view_channel and room.permissions_for(m).connect for m in members):
                continue
            access = room.permissions_for(guild.me)
            if not (access.view_channel and access.connect):
                continue
            if match["options"]["mute_voice"] and not access.mute_members:
                continue
            if match["options"].get("deaf_voice", False) and not access.deafen_members:
                continue
            if match["options"]["move_voice"] and not access.move_members:
                continue
            if room.user_limit and len([m for m in room.members if m.id not in ids]) + len(members) > room.user_limit:
                continue
            available.append(room.id)
        occupied = [m.voice.channel.id for m in members if m.voice and m.voice.channel]
        target_id = choose_voice_room(available, occupied, match["options"].get("voice_channel_id"))
        return members, text_room, guild.get_channel(target_id) if target_id else None

    async def create_rooms(self, match):
        guild = self.guild(match)
        members, room, voice = await self.room_plan(match)
        # Store ownership before binding any IDs, so recovery never deletes a reused room.
        options = {**match["options"], "owns_text_channel": room is None, "owns_voice_channel": voice is None}
        self.data.update_match(match["id"], options=options)
        if room is not None and voice is not None:
            self.data.update_match(match["id"], text_id=room.id, voice_id=voice.id)
            return
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                   embed_links=True, connect=True),
        }
        for member in members:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, connect=True, speak=True,
            )
        for user in self.bot.blitz.managers(match) - {m.id for m in members}:
            manager = await guild.fetch_member(user)
            overwrites[manager] = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, speak=True)
        if room is None:
            room = await guild.create_text_channel(
                f"blitz-{match['id']}", overwrites=overwrites,
                topic="RoundForge multiplayer blitz. Private to the participants and server administrators.",
                reason=f"Accepted blitz {match['id']}",
            )
        # Persist each resource immediately so partial setup can be recovered.
        self.data.update_match(match["id"], text_id=room.id)
        if voice is None:
            voice = await guild.create_voice_channel(
                f"Blitz {match['id']}", overwrites=overwrites,
                user_limit=len({*self.bot.blitz.managers(match), *(m.id for m in members)}),
                reason=f"Accepted blitz {match['id']}",
            )
        self.data.update_match(match["id"], voice_id=voice.id)

    async def enter(self, match, player):
        guild = self.guild(match)
        member = await guild.fetch_member(player["user_id"])
        voice = member.voice
        if not voice or not voice.channel:
            return
        target = guild.get_channel(match["voice_id"])
        if match["options"]["move_voice"] and target and voice.channel.id != target.id:
            if player["original_voice"] is None:
                self.data.update_player(match["id"], member.id, original_voice=voice.channel.id)
            await member.move_to(target, reason=f"Accepted blitz {match['id']}")
        changes = {}
        if match["options"]["mute_voice"] and not voice.mute:
            self.data.update_player(match["id"], member.id, owns_mute=1)
            changes["mute"] = True
        if match["options"].get("deaf_voice", False) and not voice.deaf:
            self.data.update_player(match["id"], member.id, owns_deaf=1)
            changes["deafen"] = True
        if changes:
            # Write ownership before the API call so partial changes survive a crash.
            await member.edit(**changes, reason=f"Blitz {match['id']}; restore voice on solve/end/cancel")

    async def announce_start(self, match):
        room = self.guild(match).get_channel(match["text_id"])
        if not room:
            raise UserError("The blitz text channel disappeared during setup.")
        result = problem_embed(match["problem"])
        ending = f"Ends <t:{match['ends_at']}:T>" if match["ends_at"] is not None else "Unlimited time"
        result.description = (
            f"**{'Solo blitz test' if match['options'].get('solo') else 'Blitz'} `{match['id']}`**\n"
            f"Starts <t:{match['starts_at']}:T> · {ending}\n"
            f"Rule: **{match['options']['end_rule']}**. Solve in individual Codeforces practice.\n"
            f"Voice room: <#{match['voice_id']}>\n"
            "Results are checked automatically. Use `/blitz check` to request another check, "
            "or `/blitz cancel` to stop and release voices.\n"
            "The bot restores only server mute/deafen changes it applied. Your own voice settings stay yours."
        )
        await room.send(embed=result)

    async def cleanup(self, match):
        guild = self.guild(match)
        terminal = match["state"] in TERMINAL
        releasing_all = terminal or match["state"] == "settling"
        for player in self.data.players(match["id"]):
            if not releasing_all and player["solved_at"] is None:
                continue
            try:
                member = await guild.fetch_member(player["user_id"])
                voice = member.voice
                if player["owns_mute"] or player["owns_deaf"]:
                    if not voice or not voice.channel:
                        # Discord cannot reliably edit a disconnected member's voice mute.
                        # Retain ownership and retry when they next join voice.
                        continue
                    changes = {}
                    # Gateway flags can lag behind an already successful REST edit.
                    # Restore owned flags idempotently before dropping recovery intent.
                    if player["owns_mute"]:
                        changes["mute"] = False
                    if player["owns_deaf"]:
                        changes["deafen"] = False
                    if changes:
                        await member.edit(**changes, reason=f"Blitz {match['id']} voice release")
                    self.data.update_player(match["id"], member.id, owns_mute=0, owns_deaf=0)
                if player["original_voice"] is not None:
                    if voice and voice.channel and voice.channel.id == match["voice_id"]:
                        original = guild.get_channel(player["original_voice"])
                        if isinstance(original, discord.VoiceChannel):
                            await member.move_to(original, reason=f"Blitz {match['id']} return to voice")
                    self.data.update_player(match["id"], member.id, original_voice=None)
            except discord.HTTPException:
                log.warning("Blitz %s: voice recovery pending for %s", match["id"], player["user_id"])
        players = self.data.players(match["id"])
        if terminal and not match["announced"]:
            room = guild.get_channel(match["text_id"]) if match["text_id"] else None
            if room:
                winner = f"<@{match['winner_id']}>" if match["winner_id"] else "No winner"
                await room.send(embed=embed(
                    f"{'Solo blitz test' if match['options'].get('solo') else 'Blitz'} {match['state']}", f"**{winner}**\n{match['outcome']}\n"
                    "Voice recovery is retried if a player disconnected or permissions were unavailable. "
                    + ("This game-created text room is removed after 24 hours."
                       if owns_room(match, "text") else "Your existing channels are preserved."),
                ))
            self.data.update_match(match["id"], announced=1)
        if terminal and not any(p["owns_mute"] or p["owns_deaf"] or p["original_voice"] is not None for p in players):
            voice_room = guild.get_channel(match["voice_id"]) if match["voice_id"] else None
            if not owns_room(match, "voice"):
                self.data.update_match(match["id"], voice_id=None)
            elif voice_room:
                # Do not disconnect a participant whose original channel was deleted or who
                # joined the match room manually. Remove the room once it is empty instead.
                if not voice_room.members:
                    await voice_room.delete(reason="Finished RoundForge blitz")
                    self.data.update_match(match["id"], voice_id=None)
            elif match["voice_id"]:
                self.data.update_match(match["id"], voice_id=None)
        if terminal and not owns_room(match, "text"):
            self.data.update_match(match["id"], text_id=None)
        elif terminal and match["closed_at"] and int(time.time()) >= match["closed_at"] + 86400:
            room = guild.get_channel(match["text_id"]) if match["text_id"] else None
            if room:
                await room.delete(reason="RoundForge blitz result room retention expired")
            self.data.update_match(match["id"], text_id=None)

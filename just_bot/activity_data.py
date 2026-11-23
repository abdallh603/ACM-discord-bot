"""Activity snapshots expose approved UI fields, never tokens or unrevealed problems."""
from __future__ import annotations

import base64
import time

from .blitz_options import voice_mode
from .domain import UserError

ACTIVITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_rooms (
    instance_id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
    tournament_id TEXT, selected_by INTEGER, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_channel_choices (
    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, tournament_id TEXT NOT NULL,
    PRIMARY KEY(guild_id,channel_id)
);
"""


class ActivityData:
    def __init__(self, bot):
        self.bot, self.db = bot, bot.store.db
        self.db.executescript(ACTIVITY_SCHEMA)

    def remember_launch(self, guild, channel, tournament_id):
        with self.db:
            self.db.execute("INSERT INTO activity_channel_choices VALUES (?,?,?) ON CONFLICT(guild_id,channel_id) "
                            "DO UPDATE SET tournament_id=excluded.tournament_id", (guild, channel, tournament_id))

    def selected(self, context):
        row = self.db.execute("SELECT * FROM activity_rooms WHERE instance_id=?", (context["instance_id"],)).fetchone()
        if row:
            if row["guild_id"] != context["guild_id"] or row["channel_id"] != context["channel_id"]:
                raise UserError("This activity session belongs to another voice channel.")
            if row["updated_at"] < int(time.time()) - 3600:
                with self.db:
                    self.db.execute("UPDATE activity_rooms SET updated_at=? WHERE instance_id=?", (int(time.time()), context["instance_id"]))
            return row["tournament_id"]
        preset = self.db.execute("SELECT tournament_id FROM activity_channel_choices WHERE guild_id=? AND channel_id=?",
                                 (context["guild_id"], context["channel_id"])).fetchone()
        selected = preset[0] if preset else None
        with self.db:
            self.db.execute("INSERT INTO activity_rooms VALUES (?,?,?,?,?,?)",
                            (context["instance_id"], context["guild_id"], context["channel_id"], selected, None, int(time.time())))
            # Discord instance IDs are not reused; old shared selections have no future use.
            self.db.execute("DELETE FROM activity_rooms WHERE updated_at<?", (int(time.time()) - 604800,))
        return selected

    def visible(self, context, member):
        guild = member.guild
        tournaments = [dict(r) for r in self.db.execute(
            "SELECT id FROM tournaments WHERE guild_id=? ORDER BY created_at DESC LIMIT 100", (context["guild_id"],)
        ).fetchall()]
        result = []
        for row in tournaments:
            tournament = self.bot.tournaments.data.get(context["guild_id"], row["id"])
            channel = guild.get_channel(tournament["channel_id"])
            if channel and channel.permissions_for(member).view_channel:
                result.append(tournament)
        return result

    def can_manage(self, tournament, member):
        return self.bot.tournaments.can_manage(tournament, member.id, member.guild_permissions.manage_guild)

    def choose(self, context, member, tournament_id):
        choices = {t["id"]: t for t in self.visible(context, member)}
        if tournament_id not in choices:
            raise UserError("This tournament is unavailable or you cannot view its text channel.")
        current = self.selected(context)
        can_select = member.guild_permissions.manage_guild
        if current in choices:
            can_select |= self.can_manage(choices[current], member)
        elif not current:
            can_select |= self.can_manage(choices[tournament_id], member)
        if not can_select:
            raise UserError("The tournament host or a server organizer chooses the shared bracket.")
        with self.db:
            self.db.execute("UPDATE activity_rooms SET tournament_id=?,selected_by=?,updated_at=? WHERE instance_id=?",
                            (tournament_id, member.id, int(time.time()), context["instance_id"]))

    def snapshot(self, context, member):
        choices = self.visible(context, member)
        selected = self.selected(context)
        tournament = next((t for t in choices if t["id"] == selected), None)
        can_select = member.guild_permissions.manage_guild or (
            self.can_manage(tournament, member) if tournament else
            not selected and any(self.can_manage(t, member) for t in choices))
        result = {"preview": False, "viewer": {"id": str(member.id), "username": member.name},
                  "room": {"id": str(context["channel_id"]), "name": context.get("channel_name", "Voice room"),
                           "instance_id": context["instance_id"], "can_select": bool(can_select)},
                  "tournaments": [{"id": t["id"], "name": t["name"], "state": t["state"],
                                   "can_manage": self.can_manage(t, member)} for t in choices],
                  "tournament": None, "players": [], "pairs": [], "games": {}}
        if tournament is None:
            return result
        options = tournament["options"]
        result["tournament"] = {key: tournament[key] for key in ("id", "name", "state", "capacity", "current_round")}
        result["tournament"].update(champion_id=str(tournament["champion_id"]) if tournament["champion_id"] else None,
            creator_id=str(tournament["creator_id"]), can_manage=self.can_manage(tournament, member),
            minutes=options["minutes"], voice_mode=voice_mode(options),
            random_topic=options["random_topic"],
            rating=options["rating_min"] or options["rating_max"], topics=options.get("tags") or ([options["tag"]] if options["tag"] else []))
        pictures = self.bot.tournaments.platform.pictures
        for entry in self.bot.tournaments.data.entries(tournament["id"]):
            identity = pictures.cache.get((context["guild_id"], entry["user_id"], entry["handle"].casefold()))
            identity = identity[1] if identity else {}
            player = {"id": str(entry["user_id"]), "username": identity.get("username", f"Member {entry['user_id']}"),
                      "handle": entry["handle"]}
            for name in ("discord_avatar", "cf_avatar"):
                raw = identity.get(name)
                player[name] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii") if raw else None
            result["players"].append(player)
        for pair in self.bot.tournaments.data.pairs(tournament["id"]):
            item = {key: pair[key] for key in ("round_no", "position", "state", "blitz_id", "note")}
            item.update({key: str(pair[key]) if pair[key] is not None else None for key in ("player_a", "player_b", "winner_id")})
            result["pairs"].append(item)
            if not pair["blitz_id"]:
                continue
            match = self.bot.activities.match(context["guild_id"], pair["blitz_id"])
            result["games"][match["id"]] = {key: match[key] for key in ("id", "state", "starts_at", "ends_at", "outcome")}
            game = result["games"][match["id"]]
            game.update(revision=match["options"]["revision"], countdown_ends_at=match["options"].get("countdown_ends_at"),
                        winner_id=str(match["winner_id"]) if match["winner_id"] else None,
                        players=[{"id": str(p["user_id"]), "ready": p["accepted_revision"] == match["options"]["revision"],
                                  "solved_at": p["solved_at"]} for p in self.bot.activities.players(match["id"])])
            # Selection stores the problem before the countdown: keep it server-side until GO.
            game["problem"] = ({key: match["problem"].get(key) for key in ("contestId", "index", "name", "rating", "tags")}
                               if match["problem"] and match["starts_at"] is not None
                               and match["state"] not in {"pending", "starting", "countdown"} else None)
        return result

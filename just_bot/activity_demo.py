"""Explicit fictional review mode; never imports settings, .env, a bot token, or a live database."""
from __future__ import annotations

import copy
import time

from .activity_server import ActivityError


class DemoActivity:
    preview = True

    def __init__(self):
        self.reset("running")

    def config(self):
        return {"preview": True, "client_id": None, "server_time": time.time()}

    async def login(self, body):
        return {"session_token": "preview-session", "expires_in": 86400, "server_time": time.time()}

    def reset(self, scenario):
        people = [("ahmad", "Ahmad_CF"), ("sara", "Sara_Codes"), ("omar", "Omar_CF"), ("lina", "Lina_Codes"),
                  ("yousef", "Yousef_CF"), ("noor", "Noor_Codes"), ("zaid", "Zaid_CF"), ("hala", "Hala_Codes")]
        self.value = {"preview": True, "viewer": {"id": "1", "username": "ahmad"},
            "room": {"id": "sample-room", "name": "The practice room", "instance_id": "sample-instance", "can_select": True},
            "tournaments": [{"id": "community-cup", "name": "Community Cup", "state": scenario, "can_manage": True}],
            "tournament": {"id": "community-cup", "name": "Community Cup", "state": scenario, "capacity": 8,
                "current_round": 0 if scenario == "registration" else 2, "champion_id": None, "creator_id": "1",
                "can_manage": True, "minutes": 20, "voice_mode": "mute", "rating": 800, "topics": ["brute force", "math"]},
            "players": [{"id": str(i), "username": name, "handle": handle, "discord_avatar": None, "cf_avatar": None}
                        for i, (name, handle) in enumerate(people, 1)], "pairs": [], "games": {},
            "viewers": [{"id": "1", "username": "ahmad"}, {"id": "4", "username": "lina"}, {"id": "6", "username": "noor"}]}
        if scenario == "registration":
            return
        for pos, (a, b, winner) in enumerate(((1, 2, 1), (3, 4, 4), (5, 6, 6), (7, 8, 8)), 1):
            self.value["pairs"].append({"round_no": 1, "position": pos, "player_a": str(a), "player_b": str(b),
                "winner_id": str(winner), "state": "done", "blitz_id": f"quarter{pos}", "note": "Verified Blitz result."})
        for pos, (a, b, state) in enumerate(((1, 4, "pending"), (6, 8, "active")), 1):
            mid = f"semi{pos}"
            self.value["pairs"].append({"round_no": 2, "position": pos, "player_a": str(a), "player_b": str(b),
                "winner_id": None, "state": "playing", "blitz_id": mid, "note": ""})
            self.value["games"][mid] = {"id": mid, "state": state, "starts_at": int(time.time()) - 420 if state == "active" else None,
                "ends_at": int(time.time()) + 780 if state == "active" else None, "revision": 1, "countdown_ends_at": None,
                "winner_id": None, "outcome": None, "problem": self.problem() if state == "active" else None,
                "players": [{"id": str(a), "ready": state == "active", "solved_at": None},
                            {"id": str(b), "ready": True, "solved_at": None}]}
        if scenario == "finished":
            for pos, winner in ((1, "1"), (2, "6")):
                self.value["pairs"][3 + pos].update(state="done", winner_id=winner)
                self.value["games"][f"semi{pos}"].update(state="finished", winner_id=winner)
            self.value["pairs"].append({"round_no": 3, "position": 1, "player_a": "1", "player_b": "6",
                "winner_id": "1", "state": "done", "blitz_id": None, "note": "Verified Blitz result."})
            self.value["tournament"].update(current_round=3, champion_id="1")

    @staticmethod
    def problem():
        return {"contestId": 4, "index": "A", "name": "Watermelon", "rating": 800, "tags": ["brute force", "math"]}

    def check(self, request):
        if request.headers.get("Authorization") != "Bearer preview-session":
            raise ActivityError("Open the local preview to begin.", 401)

    async def snapshot(self, request):
        self.check(request)
        for game in self.value["games"].values():
            if game["state"] == "countdown" and game["countdown_ends_at"] <= time.time():
                start = game["countdown_ends_at"]
                game.update(state="active", starts_at=start, ends_at=start + 1200, problem=self.problem())
        return copy.deepcopy(self.value)

    async def action(self, request, body):
        self.check(request)
        action = body.get("action")
        if action == "demo_scenario" and body.get("scenario") in {"registration", "running", "finished"}:
            self.reset(body["scenario"])
        elif action == "ready":
            game = self.value["games"].get(body.get("match_id"))
            if not game or game["state"] != "pending" or game["revision"] != body.get("revision"):
                raise ActivityError("This sample match is no longer waiting for acceptance.", 400)
            game["players"][0]["ready"] = True
            game.update(state="countdown", countdown_ends_at=int(time.time()) + 10)
        elif action == "decline":
            game = self.value["games"].get(body.get("match_id"))
            if game:
                game.update(state="cancelled", outcome="Declined in the preview.")
                for pair in self.value["pairs"]:
                    if pair["blitz_id"] == game["id"]:
                        pair.update(state="blocked", note="Declined invitation; manager action needed.")
        elif action == "start":
            if len(self.value["players"]) < 2:
                raise ActivityError("At least two players must join.", 400)
            registered = copy.deepcopy(self.value["players"])
            self.reset("running")
            if len(registered) != 8:
                from .tournament import seed_order
                size = 1 << (len(registered) - 1).bit_length()
                users = [p["id"] for p in registered] + [None] * (size - len(registered))
                placed = [users[i - 1] for i in seed_order(size)]
                self.value.update(players=registered, pairs=[], games={})
                self.value["tournament"]["current_round"] = 1
                for pos in range(size // 2):
                    a, b = placed[pos * 2:pos * 2 + 2]
                    mid = f"first{pos}"
                    self.value["pairs"].append({"round_no": 1, "position": pos + 1, "player_a": a, "player_b": b,
                        "winner_id": (a or b) if not a or not b else None, "state": "done" if not a or not b else "playing",
                        "note": "Bye" if not a or not b else "", "blitz_id": mid if a and b else None})
                    if a and b:
                        self.value["games"][mid] = {"id": mid, "state": "active", "starts_at": int(time.time()),
                            "ends_at": int(time.time()) + 1200, "revision": 1, "countdown_ends_at": None,
                            "winner_id": None, "outcome": None, "problem": self.problem(),
                            "players": [{"id": a, "ready": True, "solved_at": None}, {"id": b, "ready": True, "solved_at": None}]}
        elif action == "join":
            if not any(p["id"] == "1" for p in self.value["players"]):
                self.value["players"].insert(0, {"id": "1", "username": "ahmad", "handle": "Ahmad_CF"})
        elif action == "leave":
            self.value["players"] = [p for p in self.value["players"] if p["id"] != "1"]
        elif action == "cancel_tournament":
            self.value["tournament"]["state"] = "cancelled"
            for game in self.value["games"].values():
                game.update(state="cancelled", outcome="Tournament cancelled in preview.")
            for pair in self.value["pairs"]:
                if pair["state"] != "done":
                    pair.update(state="blocked", note="Tournament cancelled.")
        elif action not in {"check", "select"}:
            raise ActivityError("Unknown preview action.", 400)
        self.value["tournaments"][0]["state"] = self.value["tournament"]["state"]
        return await self.snapshot(request)

    async def close(self):
        pass

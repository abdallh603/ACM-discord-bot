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
        self.retry_count = 0
        state = "running" if scenario == "blocked" else scenario
        people = [("ahmad", "Ahmad_CF"), ("sara", "Sara_Codes"), ("omar", "Omar_CF"), ("lina", "Lina_Codes"),
                  ("yousef", "Yousef_CF"), ("noor", "Noor_Codes"), ("zaid", "Zaid_CF"), ("hala", "Hala_Codes")]
        self.value = {"preview": True, "viewer": {"id": "1", "username": "ahmad"},
            "room": {"id": "sample-room", "name": "The practice room", "instance_id": "sample-instance", "can_select": True},
            "tournaments": [{"id": "community-cup", "name": "Community Cup", "state": state, "can_manage": True}],
            "tournament": {"id": "community-cup", "name": "Community Cup", "state": state, "capacity": 8,
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
                "minutes": 20,
                "winner_id": None, "outcome": None, "problem": self.problem() if state == "active" else None,
                "players": [{"id": str(a), "ready": state == "active", "solved_at": None},
                            {"id": str(b), "ready": True, "solved_at": None}]}
        if scenario == "blocked":
            self.value["games"]["semi1"].update(state="cancelled", outcome="No verified winner in the sample match.")
            self.value["pairs"][4].update(state="blocked", note="No verified winner; choose a retry or a manager decision.")
        elif scenario == "finished":
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
        if action == "demo_scenario" and body.get("scenario") in {"registration", "running", "blocked", "finished"}:
            self.reset(body["scenario"])
        elif action in {"retry_match", "resolve_match"}:
            pair = next((p for p in self.value["pairs"] if p["round_no"] == body.get("round_no") and p["position"] == body.get("position")), None)
            if not pair or pair["state"] != "blocked" or pair["blitz_id"] != body.get("expected_match_id"):
                raise ActivityError("Review the current blocked match before changing it.", 400)
            if action == "resolve_match":
                winner = body.get("winner_id")
                if winner not in {pair["player_a"], pair["player_b"]}:
                    raise ActivityError("Choose one of this pairing's players.", 400)
                pair.update(state="done", winner_id=winner, note="Manager decision by 1 in the local preview.")
                self.advance_round()
            else:
                self.retry_count += 1
                mid = f"retry{self.retry_count}"
                pair.update(state="playing", blitz_id=mid, note="Fresh sample attempt.")
                self.new_game(mid, pair["player_a"], pair["player_b"])
        elif action in {"set_time", "cancel_match"}:
            game = self.value["games"].get(body.get("match_id"))
            if not game:
                raise ActivityError("Choose an existing sample match.", 400)
            if action == "set_time":
                minutes = body.get("minutes")
                if game["state"] != "active" or "minutes" not in body or (minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 180)):
                    raise ActivityError("Use 1–180 minutes or unlimited for an active match.", 400)
                if "expected_ends_at" not in body or game["ends_at"] != body["expected_ends_at"]:
                    raise ActivityError("The timer changed. Review the current sample match.", 400)
                game.update(minutes=minutes, ends_at=game["starts_at"] + minutes * 60 if minutes is not None else None)
                if game["ends_at"] is not None and game["ends_at"] <= time.time():
                    game["state"] = "settling"
            else:
                game.update(state="cancelled", outcome="Match stopped by the preview manager.")
                for pair in self.value["pairs"]:
                    if pair["blitz_id"] == game["id"]:
                        pair.update(state="blocked", note="Stopped by the manager; retry or choose who advances.")
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

    def new_game(self, mid, a, b):
        own = "1" in {a, b}
        self.value["games"][mid] = {"id": mid, "state": "pending" if own else "active", "revision": 1,
            "starts_at": None if own else int(time.time()), "ends_at": None if own else int(time.time()) + 1200,
            "minutes": 20, "countdown_ends_at": None, "winner_id": None, "outcome": None,
            "problem": None if own else self.problem(),
            "players": [{"id": user, "ready": user != "1", "solved_at": None} for user in (a, b)]}

    def advance_round(self):
        current = self.value["tournament"]["current_round"]
        pairs = [p for p in self.value["pairs"] if p["round_no"] == current]
        if not pairs or any(p["state"] != "done" for p in pairs):
            return
        winners = [p["winner_id"] for p in sorted(pairs, key=lambda p: p["position"])]
        if len(winners) == 1:
            self.value["tournament"].update(state="finished", champion_id=winners[0])
            return
        self.value["tournament"]["current_round"] = current + 1
        for pos in range(len(winners) // 2):
            a, b = winners[pos * 2:pos * 2 + 2]
            mid = f"round{current + 1}match{pos + 1}"
            self.value["pairs"].append({"round_no": current + 1, "position": pos + 1, "player_a": a, "player_b": b,
                "winner_id": None, "state": "playing", "blitz_id": mid, "note": ""})
            self.new_game(mid, a, b)

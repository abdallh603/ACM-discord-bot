"""Same-process Activity server and a separate, loopback-only review preview."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiohttp import web

from just_bot.core.api import UserError

from .data import ActivityData

log = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("web")
API = "https://discord.com/api/v10"
KEY = web.AppKey("activity_service", object)


class ActivityError(Exception):
    def __init__(self, message, status=403):
        self.message, self.status = message, status


@dataclass
class Session:
    user_id: int
    instance_id: str
    context: dict
    expires: float
    last_action: float = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class DiscordGateway:
    def __init__(self, settings):
        self.settings = settings
        self.http = None

    async def request(self, method, path, *, bearer=None, runtime=False, data=None):
        if self.http is None:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        headers = {"Authorization": f"Bot {self.settings.token}"} if runtime else (
            {"Authorization": f"Bearer {bearer}"} if bearer else {})
        base = "https://discord.com/api" if path.startswith("/oauth2/") else API
        async with self.http.request(method, base + path, headers=headers, data=data) as response:
            if response.status >= 400:
                raise ActivityError("Discord could not verify this activity session. Reopen it from your voice room.",
                                    503 if response.status >= 500 or response.status == 429 else 401)
            value = await response.json()
            if not isinstance(value, dict):
                raise ActivityError("Discord returned an unexpected response. Try reconnecting.", 503)
            return value

    async def exchange(self, code):
        token = await self.request("POST", "/oauth2/token", data={"client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret, "grant_type": "authorization_code", "code": code})
        access = token.get("access_token")
        if not isinstance(access, str) or "identify" not in str(token.get("scope", "")).split():
            raise ActivityError("Discord authorization did not include profile access.", 401)
        authorization = await self.request("GET", "/oauth2/@me", bearer=access)
        if str(authorization.get("application", {}).get("id")) != self.settings.client_id:
            raise ActivityError("This authorization belongs to a different application.", 401)
        user = await self.request("GET", "/users/@me", bearer=access)
        return access, user, min(int(token.get("expires_in", 1800)), 1800)

    async def instance(self, instance_id):
        return await self.request("GET", f"/applications/{self.settings.client_id}/activity-instances/{quote(instance_id, safe='')}", runtime=True)

    async def close(self):
        if self.http:
            await self.http.close()


class ActivityService:
    preview = False

    def __init__(self, runtime, gateway=None):
        self.runtime, self.data = runtime, ActivityData(runtime)
        self.gateway = gateway or DiscordGateway(runtime.settings)
        self.sessions, self.instances, self.members = {}, {}, {}
        self.auth_slots = asyncio.Semaphore(4)
        self.auth_waiting = 0

    def config(self):
        return {"preview": False, "client_id": self.runtime.settings.client_id, "server_time": time.time()}

    async def context(self, instance_id, user_id, *, fresh=False):
        now = time.monotonic()
        cached = self.instances.get(instance_id)
        if fresh or not cached or cached[0] < now:
            instance = await self.gateway.instance(instance_id)
            if str(instance.get("application_id")) != self.runtime.settings.client_id or instance.get("instance_id") != instance_id:
                raise ActivityError("This is not an active RoundForge instance.", 401)
            cached = (now + 20, instance)
            self.instances[instance_id] = cached
            if len(self.instances) > 4096:
                self.instances = {key: value for key, value in self.instances.items() if value[0] > now}
                while len(self.instances) > 4096:
                    self.instances.pop(next(iter(self.instances)))
        instance = cached[1]
        location = instance.get("location", {})
        users = {str(user) for user in instance.get("users", [])}
        if str(user_id) not in users or not str(location.get("guild_id", "")).isdigit():
            raise ActivityError("Join this activity in a server voice channel before connecting.", 401)
        guild_id, channel_id = int(location["guild_id"]), int(location.get("channel_id") or 0)
        guild = self.runtime.get_guild(guild_id)
        if not self.runtime.guild_enabled(guild_id) or guild is None:
            raise ActivityError("RoundForge is not enabled in this server.")
        channel = guild.get_channel(channel_id)
        channel_type = getattr(channel, "type", None)
        if channel is None or getattr(channel_type, "value", channel_type) not in {2, 13}:
            raise ActivityError("Launch RoundForge from a server voice channel.")
        return {"guild_id": guild_id, "channel_id": channel_id, "channel_name": channel.name, "instance_id": instance_id}

    async def member(self, context, user_id, *, fresh=False):
        key = context["guild_id"], user_id
        cached = self.members.get(key)
        if fresh or not cached or cached[0] < time.monotonic():
            guild = self.runtime.get_guild(context["guild_id"])
            if guild is None:
                raise ActivityError("The server is no longer available.")
            try:
                member = await guild.fetch_member(user_id)
            except Exception as exc:
                raise ActivityError("Your server membership could not be verified.") from exc
            cached = time.monotonic() + 20, member
            self.members[key] = cached
            if len(self.members) > 4096:
                self.members = {key: value for key, value in self.members.items() if value[0] > time.monotonic()}
                while len(self.members) > 4096:
                    self.members.pop(next(iter(self.members)))
        member = cached[1]
        channel = member.guild.get_channel(context["channel_id"])
        if channel is None or not channel.permissions_for(member).view_channel:
            raise ActivityError("You no longer have access to this voice channel.")
        return member

    async def login(self, body):
        code, instance_id = body.get("code"), body.get("instance_id")
        if not isinstance(code, str) or not 1 <= len(code) <= 4096:
            raise ActivityError("A Discord authorization code is required.", 400)
        if not isinstance(instance_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", instance_id):
            raise ActivityError("A valid activity instance is required.", 400)
        if self.auth_waiting >= 8:
            raise ActivityError("Activity sign-in is busy. Try again shortly.", 429)
        self.auth_waiting += 1
        try:
            async with self.auth_slots:
                access, user, ttl = await self.gateway.exchange(code)
                user_id = int(user["id"])
                context = await self.context(instance_id, user_id, fresh=True)
                await self.member(context, user_id, fresh=True)
        finally:
            self.auth_waiting -= 1
        now = time.monotonic()
        self.sessions = {token: session for token, session in self.sessions.items() if session.expires > now}
        if len(self.sessions) >= 4096:
            raise ActivityError("Activity capacity reached. Try again later.", 503)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = Session(user_id, instance_id, context, now + max(1, ttl))
        return {"access_token": access, "session_token": token, "expires_in": ttl, "server_time": time.time()}

    async def authenticate(self, request, *, fresh=False):
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        session = self.sessions.get(token)
        if session is None or session.expires <= time.monotonic():
            self.sessions.pop(token, None)
            raise ActivityError("Your activity session expired. Reconnect to continue.", 401)
        session.context = await self.context(session.instance_id, session.user_id, fresh=fresh)
        member = await self.member(session.context, session.user_id, fresh=fresh)
        return session, member

    async def snapshot(self, request):
        session, member = await self.authenticate(request)
        return self.data.snapshot(session.context, member)

    async def action(self, request, body):
        session, member = await self.authenticate(request, fresh=True)
        if session.lock.locked() or time.monotonic() - session.last_action < 1:
            raise ActivityError("An action is already in progress. Wait a moment.", 429)
        async with session.lock:
            session.last_action = time.monotonic()
            action, tid = body.get("action"), body.get("tournament_id")
            if not isinstance(tid, str):
                raise ActivityError("Choose a tournament first.", 400)
            if action == "select":
                self.data.choose(session.context, member, tid)
                return self.data.snapshot(session.context, member)
            choices = {t["id"]: t for t in self.data.visible(session.context, member)}
            if tid not in choices or self.data.selected(session.context) != tid:
                raise ActivityError("This tournament is not the room's current shared bracket.")
            guild, actor = session.context["guild_id"], session.user_id
            organizer = member.guild_permissions.manage_guild
            if action == "join":
                await self.runtime.tournaments.engine.join(guild, tid, actor)
            elif action == "leave":
                await self.runtime.tournaments.engine.leave(guild, tid, actor)
            elif action == "start":
                await self.runtime.tournaments.engine.start(guild, tid, actor, organizer=organizer)
            elif action == "cancel_tournament":
                await self.runtime.tournaments.engine.cancel(guild, tid, actor, organizer=organizer)
            elif action in {"retry_match", "resolve_match", "set_time", "cancel_match"}:
                if choices[tid]["state"] != "running" or not self.data.can_manage(choices[tid], member):
                    raise ActivityError("Only this tournament's managers or a server organizer can use these controls.")
                if action in {"retry_match", "resolve_match"}:
                    r, position = body.get("round_no"), body.get("position")
                    if type(r) is not int or type(position) is not int or not 1 <= r <= 5 or not 1 <= position <= 16:
                        raise ActivityError("Select a valid bracket pairing.", 400)
                    if "expected_match_id" not in body:
                        raise ActivityError("Review the current match attempt first.", 400)
                    options = {"organizer": organizer, "expected_match_id": body["expected_match_id"]}
                    if action == "retry_match":
                        await self.runtime.tournaments.engine.retry(guild, tid, actor, r, position, **options)
                    else:
                        winner = body.get("winner_id")
                        if not isinstance(winner, str) or not winner.isascii() or not winner.isdigit() or len(winner) > 20:
                            raise ActivityError("Choose one of this pairing's players.", 400)
                        await self.runtime.tournaments.engine.resolve(guild, tid, actor, r, position, int(winner), **options)
                else:
                    mid = body.get("match_id")
                    if mid not in {p["blitz_id"] for p in self.runtime.tournaments.store.pairs(tid) if p["blitz_id"]}:
                        raise ActivityError("This match is not part of the shared tournament.")
                    if action == "cancel_match":
                        await self.runtime.blitz.engine.cancel(guild, mid, actor, organizer=organizer)
                        await self.runtime.tournaments.engine.advance(guild, tid)
                    else:
                        minutes = body.get("minutes")
                        if "minutes" not in body or (minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 180)):
                            raise ActivityError("Use 1–180 minutes, or select unlimited time.", 400)
                        deadline = body.get("expected_ends_at")
                        if "expected_ends_at" not in body or (deadline is not None and type(deadline) is not int):
                            raise ActivityError("Review the current timer before changing it.", 400)
                        await self.runtime.blitz.engine.set_time(guild, mid, minutes, actor=actor, organizer=organizer, expected_ends_at=deadline)
            elif action in {"ready", "decline", "check"}:
                if choices[tid]["state"] != "running":
                    raise ActivityError("This tournament is no longer running.", 400)
                mid = body.get("match_id")
                if mid not in {p["blitz_id"] for p in self.runtime.tournaments.store.pairs(tid) if p["blitz_id"]}:
                    raise ActivityError("This match is not part of the shared tournament.")
                match = self.runtime.blitz.store.match(guild, mid)
                if not self.runtime.blitz.engine.can_access(match, actor) and not organizer:
                    raise ActivityError("Only this match's players and managers can control it.")
                if action == "ready":
                    revision = body.get("revision")
                    if not isinstance(revision, int) or isinstance(revision, bool):
                        raise ActivityError("Review the latest settings before accepting.", 400)
                    if match["options"].get("move_voice", True):
                        target = match["options"].get("voice_channel_id")
                        room = session.context["channel_id"]
                        if target and target != room:
                            raise ActivityError("Open the activity in the tournament's selected game voice room.", 400)
                        if not target:
                            states = member.guild.voice_states
                            if any(getattr(getattr(states.get(p["user_id"]), "channel", None), "id", None) != room
                                   for p in self.runtime.blitz.store.players(mid)):
                                raise ActivityError("Both players must join the same voice room before using Ready here. You can also use the bot's regular Accept button.", 400)
                    await self.runtime.blitz.engine.accept(guild, mid, actor, revision)
                elif action == "decline":
                    if match["state"] != "pending" or actor not in {p["user_id"] for p in self.runtime.blitz.store.players(mid)}:
                        raise ActivityError("Only an invited player can decline a pending match.")
                    await self.runtime.blitz.engine.cancel(guild, mid, actor)
                else:
                    self.runtime.blitz.engine.request_check(guild, mid)
            else:
                raise ActivityError("Unknown activity action.", 400)
            return self.data.snapshot(session.context, member)

    async def close(self):
        self.sessions.clear()
        await self.gateway.close()


@web.middleware
async def policy(request, handler):
    service = request.app[KEY]
    origin = request.headers.get("Origin")
    if origin and request.path.startswith("/api/") and request.method == "POST":
        expected = f"https://{service.config().get('client_id')}.discordsays.com"
        allowed = {expected} if not service.preview else {f"http://{request.host}"}
        if origin not in allowed:
            return web.json_response({"error": "This origin is not allowed."}, status=403)
    try:
        response = await handler(request)
    except ActivityError as exc:
        response = web.json_response({"error": exc.message}, status=exc.status)
    except UserError as exc:
        response = web.json_response({"error": str(exc)}, status=400)
    except (TimeoutError, aiohttp.ClientError):
        response = web.json_response({"error": "Discord is unavailable. The bracket will reconnect."}, status=503)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("Activity request failed")
        response = web.json_response({"error": "The activity could not complete that request."}, status=500)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'self' https://discord.com https://*.discord.com https://*.discordapp.com; base-uri 'none'; object-src 'none'")
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Server-Time"] = str(time.time())
    return response


async def body(request):
    if request.content_type != "application/json":
        raise ActivityError("Use a JSON request body.", 415)
    try:
        value = await request.json()
    except (ValueError, UnicodeError) as exc:
        raise ActivityError("Invalid JSON request.", 400) from exc
    if not isinstance(value, dict):
        raise ActivityError("Use a JSON object.", 400)
    return value


def create_app(service):
    app = web.Application(middlewares=[policy], client_max_size=16 * 1024)
    app[KEY] = service
    async def config(request):
        return web.json_response(service.config())
    async def login(request):
        return web.json_response(await service.login(await body(request)))
    async def snapshot(request):
        value = await service.snapshot(request)
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
        headers = {"ETag": etag, "X-Server-Time": str(time.time())}
        return web.Response(status=304, headers=headers) if request.headers.get("If-None-Match") == etag else web.Response(body=raw, content_type="application/json", headers=headers)
    async def action(request):
        return web.json_response(await service.action(request, await body(request)))
    async def index(request):
        return web.FileResponse(STATIC / "index.html")
    app.router.add_get("/api/config", config)
    app.router.add_post("/api/session", login)
    app.router.add_get("/api/snapshot", snapshot)
    app.router.add_post("/api/action", action)
    app.router.add_get("/", index)
    for filename in ("app.js", "style.css", "model.js"):
        async def asset(request, name=filename):
            return web.FileResponse(STATIC / name)
        app.router.add_get("/" + filename, asset)
    # No general filesystem route: .env, SQLite, source, and repository files cannot be served.
    async def sdk(request):
        file = STATIC / "vendor" / "discord-sdk.js"
        if not file.is_file():
            raise web.HTTPNotFound(text="Build the Discord SDK with npm run build:sdk in extensions/activity/client.")
        return web.FileResponse(file)
    app.router.add_get("/vendor/discord-sdk.js", sdk)
    async def notices(request):
        file = STATIC / "vendor" / "discord-sdk-license.txt"
        if not file.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file)
    app.router.add_get("/vendor/discord-sdk-license.txt", notices)
    async def cleanup(app):
        await service.close()
    app.on_cleanup.append(cleanup)
    return app


async def start_server(runtime):
    from .setup_cli import sdk_status
    ready, message = sdk_status()
    if not ready:
        raise ValueError(message)
    service = ActivityService(runtime)
    runner = web.AppRunner(create_app(service), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, runtime.settings.host, runtime.settings.port).start()
    except Exception:
        await runner.cleanup()
        raise
    log.info("Voice activity server listening on %s:%s", runtime.settings.host, runtime.settings.port)
    return runner


def main():
    parser = argparse.ArgumentParser(description="Review RoundForge's voice activity locally with fictional players.")
    parser.add_argument("--preview", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    from .demo import DemoActivity
    web.run_app(create_app(DemoActivity()), host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()

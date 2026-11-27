"""Credential-safe local setup and deployment checks; never prints secret values."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import discord
from dotenv import dotenv_values, set_key

from .server import STATIC

def sdk_status(directory=STATIC / "vendor"):
    bundle, notice, manifest = (directory / name for name in ("discord-sdk.js", "discord-sdk-license.txt", "sdk-build.json"))
    if not bundle.is_file() or not notice.is_file() or not manifest.is_file():
        return False, "Build the official SDK with setup-activity.ps1 or extensions/activity/client/npm run build:sdk."
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False, "The SDK build manifest has an invalid format; rebuild it."
        raw = bundle.read_bytes()
        if len(raw) < 10_000 or hashlib.sha256(raw).hexdigest() != data.get("sha256") or not notice.stat().st_size:
            return False, "The SDK bundle or notices are incomplete/changed; rebuild them."
        return True, f"Official SDK {data.get('sdk_version', 'unknown')} is built and its checksum matches."
    except (OSError, ValueError):
        return False, "The SDK build manifest could not be verified; rebuild it."


def local_checks(values, directory=STATIC / "vendor"):
    ok, message = sdk_status(directory)
    client = str(values.get("DISCORD_CLIENT_ID") or "")
    port = str(values.get("ACTIVITY_PORT") or "8080")
    checks = [{"name": "SDK bundle", "ok": ok, "message": message},
        {"name": "Bot token", "ok": bool(values.get("DISCORD_TOKEN")), "message": "Present locally" if values.get("DISCORD_TOKEN") else "Set the bot token locally."},
        {"name": "Application ID", "ok": bool(client and client.isascii() and client.isdigit()), "message": "Numeric application ID is set." if client and client.isascii() and client.isdigit() else "Set the application's numeric ID locally."},
        {"name": "OAuth client secret", "ok": bool(values.get("DISCORD_CLIENT_SECRET")), "message": "Present locally" if values.get("DISCORD_CLIENT_SECRET") else "Enter it with --configure; do not send it in chat."},
        {"name": "HTTP port", "ok": port.isascii() and port.isdigit() and 1 <= int(port) <= 65535, "message": "Use a port between 1 and 65535."}]
    enabled = str(values.get("ACTIVITY_ENABLED") or "false").lower() in {"true", "1"}
    checks.append({"name": "Activity enabled locally", "ok": enabled, "message": "Enabled" if enabled else "Enable it after the SDK and OAuth settings are ready."})
    return checks


def public_origin(value):
    try:
        url = urlparse(value)
        _ = url.port
        if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
                or url.path not in {"", "/"} or url.hostname in {"localhost", "127.0.0.1", "::1"}):
            return None
        return f"https://{url.netloc}"
    except ValueError:
        return None


async def online_checks(values, origin):
    checks = []
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        try:
            async with http.get("https://discord.com/api/v10/oauth2/applications/@me",
                                headers={"Authorization": f"Bot {values.get('DISCORD_TOKEN') or ''}"}) as response:
                if response.status != 200:
                    raise ValueError("Application lookup failed")
                application = await response.json()
                if not isinstance(application, dict):
                    raise ValueError("Invalid application response")
            same = str(application.get("id")) == values.get("DISCORD_CLIENT_ID")
            embedded = discord.ApplicationFlags._from_value(int(application.get("flags", 0))).embedded
            checks.append({"name": "Discord application", "ok": same, "message": "The bot and Activity application IDs match." if same else "The configured client ID does not match this bot."})
            checks.append({"name": "Activities enabled", "ok": bool(embedded), "message": "Enable Activities in the Discord Developer Portal if this check fails."})
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
            checks.append({"name": "Discord application", "ok": False, "message": "Could not verify the bot application. Check its token and outbound HTTPS access."})
        if not origin:
            checks.append({"name": "Public HTTPS", "ok": False, "message": "Pass --public-url https://your-activity-host (an origin with no path/query)."})
            return checks
        try:
            async with http.get(origin + "/api/config", allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError("Public config failed")
                config = await response.json()
                if not isinstance(config, dict):
                    raise ValueError("Invalid config response")
            live = config.get("preview") is False and config.get("client_id") == values.get("DISCORD_CLIENT_ID")
            checks.append({"name": "Public HTTPS", "ok": live, "message": "The HTTPS host serves the real Activity." if live else "This host serves a preview or a different application."})
            async with http.get(origin + "/api/snapshot", allow_redirects=False) as response:
                checks.append({"name": "Private API", "ok": response.status == 401, "message": "Unauthenticated tournament data must return HTTP 401."})
            async with http.get(origin + "/vendor/discord-sdk.js", allow_redirects=False) as response:
                raw = await response.content.read(4 * 1024 * 1024 + 1)
                manifest = json.loads((STATIC / "vendor/sdk-build.json").read_text(encoding="utf-8"))
                matches = response.status == 200 and hashlib.sha256(raw).hexdigest() == manifest["sha256"]
                checks.append({"name": "Hosted SDK", "ok": matches, "message": "The served SDK must match the verified local build."})
        except (aiohttp.ClientError, TimeoutError, ValueError, OSError, KeyError, TypeError):
            checks.append({"name": "Hosted Activity", "ok": False, "message": "Could not verify this HTTPS deployment. Check the host, certificate, routes, and local SDK build."})
    return checks


def configure(path, client_id=None, port=8080):
    if not sdk_status()[0]:
        raise ValueError("Build the official SDK first. Run setup-activity.ps1 in an internet-enabled terminal.")
    if not path.is_file():
        raise ValueError("Create the local .env from .env.example and set the bot token first.")
    existing = dotenv_values(path)
    client_id = client_id or existing.get("DISCORD_CLIENT_ID") or input("Discord application ID: ").strip()
    if not client_id.isascii() or not client_id.isdigit():
        raise ValueError("Use the application's numeric ID.")
    secret = getpass.getpass("OAuth client secret (hidden; blank keeps an existing secret): ").strip()
    secret = secret or existing.get("DISCORD_CLIENT_SECRET")
    if not secret or any(ord(char) < 32 for char in secret):
        raise ValueError("Enter a nonempty OAuth client secret locally.")
    if not 1 <= port <= 65535:
        raise ValueError("Use a valid port.")
    for key, value in {"DISCORD_CLIENT_ID": client_id, "DISCORD_CLIENT_SECRET": secret,
                       "ACTIVITY_ENABLED": "true", "ACTIVITY_PORT": str(port)}.items():
        set_key(str(path), key, value, quote_mode="auto")
    print("Saved the Activity settings to the local .env. No secret values were printed.")


def main():
    parser = argparse.ArgumentParser(description="Check or configure RoundForge's live Discord Activity.")
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--client-id")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--public-url")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    env_path = Path.cwd() / ".env"
    if args.configure:
        try:
            configure(env_path, args.client_id, args.port)
        except (ValueError, OSError, EOFError) as exc:
            raise SystemExit(str(exc)) from exc
    values = {**dotenv_values(env_path), **os.environ}
    checks = local_checks(values)
    if args.online:
        checks.extend(asyncio.run(online_checks(values, public_origin(args.public_url or values.get("ACTIVITY_PUBLIC_URL") or ""))))
    if args.json:
        print(json.dumps({"checks": checks, "live_oauth_and_multiplayer_test": "manual"}))
    else:
        for check in checks:
            print(f"{'OK' if check['ok'] else 'MISSING'}: {check['name']} — {check['message']}")
        print("MANUAL: enable the root URL Mapping, then test OAuth and a two-player match inside Discord. These checks do not certify a live launch.")
    raise SystemExit(0 if all(check["ok"] for check in checks) else 1)


if __name__ == "__main__":
    main()

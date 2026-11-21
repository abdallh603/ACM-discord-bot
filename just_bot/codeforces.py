from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .domain import UserError


class CodeforcesError(UserError):
    pass


def fetch_json(method: str, params: dict):
    request = Request(
        f"https://codeforces.com/api/{method}?{urlencode(params)}",
        headers={"User-Agent": "RoundForge/0.4 (Discord community bot)", "Accept": "application/json"},
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


class Codeforces:
    def __init__(self, *, transport=None, interval: float = 2.1):
        self.transport = transport or self._transport
        self.interval = interval
        self._lock = asyncio.Lock()
        self._last_call = float("-inf")
        self._cache: OrderedDict = OrderedDict()
        self._waiting = 0

    @asynccontextmanager
    async def request_slot(self):
        if self._waiting >= 64:
            raise CodeforcesError("Codeforces requests are busy. Try again shortly.")
        self._waiting += 1
        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout=45)
            except TimeoutError as exc:
                raise CodeforcesError("Codeforces requests are busy. Try again shortly.") from exc
            try:
                yield
            finally:
                self._lock.release()
        finally:
            self._waiting -= 1

    async def _transport(self, method: str, params: dict):
        return await asyncio.to_thread(fetch_json, method, params)

    async def call(self, method: str, *, ttl: int = 0, force: bool = False, **params):
        key = (method, tuple(sorted(params.items())))
        async with self.request_slot():
            cached = self._cache.get(key)
            if not force and ttl and cached and cached[0] > time.monotonic():
                self._cache.move_to_end(key)
                return cached[1]
            for attempt in range(3):
                delay = self.interval - (time.monotonic() - self._last_call)
                while delay > 0:
                    await asyncio.sleep(delay)
                    # Windows event-loop timers can wake before the requested delay.
                    delay = self.interval - (time.monotonic() - self._last_call)
                self._last_call = time.monotonic()
                try:
                    body = await self.transport(method, params)
                except HTTPError as exc:
                    if attempt < 2 and (exc.code == 429 or exc.code >= 500):
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise CodeforcesError("Codeforces is unavailable. Please try again later.") from exc
                except (URLError, TimeoutError, OSError, ValueError) as exc:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise CodeforcesError("Could not reach Codeforces. Please try again later.") from exc
                if not isinstance(body, dict):
                    raise CodeforcesError("Codeforces returned an unexpected response.")
                if body.get("status") != "OK":
                    comment = str(body.get("comment", "Unknown API error"))
                    if "limit exceeded" in comment.lower() and attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise CodeforcesError(f"Codeforces: {comment[:300]}")
                if "result" not in body:
                    raise CodeforcesError("Codeforces returned an incomplete response.")
                result = body["result"]
                if ttl:
                    self._cache[key] = (time.monotonic() + ttl, result)
                    self._cache.move_to_end(key)
                    while len(self._cache) > 64:
                        self._cache.popitem(last=False)
                return result
        raise AssertionError("Unreachable retry state")

    async def users(self, handles: list[str], *, fresh: bool = False) -> list[dict]:
        result = []
        for offset in range(0, len(handles), 50):
            result.extend(await self.call(
                "user.info", handles=";".join(handles[offset:offset + 50]), ttl=60, force=fresh
            ))
        return result

    async def problems(self) -> list[dict]:
        result = await self.call("problemset.problems", ttl=3600)
        return result["problems"]

    async def contests(self) -> list[dict]:
        return await self.call("contest.list", gym="false", ttl=120)

    async def submissions(self, handle: str, *, since: int | None = None, fresh: bool = False) -> list[dict]:
        result = []
        # Refuse incomplete history instead of recommending an already solved problem.
        for offset in range(1, 20001, 1000):
            page = await self.call("user.status", handle=handle, ttl=0 if fresh else 30,
                                   **{"from": offset, "count": 1000})
            result.extend(page)
            if len(page) < 1000 or (since is not None and page[-1]["creationTimeSeconds"] < since):
                return result
        raise CodeforcesError(
            "Submission history is too large to check completely (20,000 limit). "
            "Ask an organizer about increasing this limit."
        )

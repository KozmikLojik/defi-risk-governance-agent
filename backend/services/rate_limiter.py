"""Redis-backed fixed-window request limits with local-only in-memory fallback."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time

_INCR_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


class RateLimiter:
    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def check(self, identity: str, bucket: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        window = int(time.time()) // window_seconds
        identity_digest = hashlib.sha256(identity.encode()).hexdigest()
        key = f"guardian:rate:{bucket}:{identity_digest}:{window}"
        if self.redis:
            count = int(await self.redis.eval(_INCR_SCRIPT, 1, key, window_seconds))
        else:
            # Development/testing only. Production startup rejects this fallback.
            async with self._lock:
                count = self._counts.get(key, 0) + 1
                self._counts[key] = count
                if len(self._counts) > 10_000:
                    active_window = int(time.time()) // window_seconds
                    self._counts = {item: value for item, value in self._counts.items()
                                    if item.endswith(f":{active_window}")}
        return count <= limit, window_seconds - (int(time.time()) % window_seconds)


async def create_rate_limiter() -> RateLimiter:
    redis_url = os.getenv("REDIS_URL")
    production = os.getenv("VERCEL") or os.getenv("ENVIRONMENT", "").lower() == "production"
    if not redis_url:
        if production:
            raise RuntimeError("Production requires REDIS_URL for shared rate limiting")
        return RateLimiter()
    from redis.asyncio import Redis
    client = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3,
                            socket_timeout=3, health_check_interval=30)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        if production:
            raise RuntimeError("Redis is unavailable; refusing to start without production rate limits")
        return RateLimiter()
    return RateLimiter(client)

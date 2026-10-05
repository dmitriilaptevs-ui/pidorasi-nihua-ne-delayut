"""Redis client and readiness probe.

Redis carries rate-limit counters and short-lived coordination only; it is
never a source of financial truth.
"""

from __future__ import annotations

import redis.asyncio as redis

from .settings import settings

_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
    return _client


async def check_redis() -> bool:
    try:
        return bool(await client().ping())
    except Exception:
        return False


async def close_redis() -> None:
    if _client is not None:
        await _client.aclose()

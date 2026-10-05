"""Fixed-window request throttles backed by Redis.

Redis is not a source of financial truth, but for credential endpoints an
unreachable Redis must not silently remove protection: the check fails closed
and the endpoint answers 503 instead of allowing unlimited attempts.
"""

from __future__ import annotations

import logging

from .redis_client import client

log = logging.getLogger("rubai.throttle")


async def allow(key: str, limit: int, window_seconds: int) -> bool:
    try:
        redis = client()
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window_seconds)
        return current <= limit
    except Exception:  # noqa: BLE001 - any Redis failure must not open the gate
        log.warning("throttle backend unavailable")
        return False

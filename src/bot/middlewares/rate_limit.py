"""Per-user anti-abuse rate limit, backed by Redis.

This is *not* a paywall — the bot is free. The cap exists so a single
abusive client cannot exhaust Tesseract or external APIs (PubChem,
CosIng, OBF) for every other user.

Implemented as plain async helpers rather than aiogram middleware
because the limit varies per action (text/photo/product).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from redis import asyncio as aioredis

from src.core.config import RateLimits, get_settings

log = logging.getLogger(__name__)


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    limit: int
    retry_after_sec: int


_redis_singleton: aioredis.Redis | None = None


def _redis() -> aioredis.Redis | None:
    global _redis_singleton
    if _redis_singleton is not None:
        return _redis_singleton
    url = get_settings().redis_url
    if not url:
        return None
    try:
        _redis_singleton = aioredis.from_url(url, decode_responses=True)
    except Exception:
        log.exception("Cannot connect to Redis for rate limiting")
        return None
    return _redis_singleton


async def _check(
    telegram_id: int, action: str, limit: int, window_sec: int
) -> RateLimitResult:
    redis = _redis()
    if redis is None:
        return RateLimitResult(allowed=True, remaining=limit, limit=limit, retry_after_sec=0)

    key = f"rl:{action}:{telegram_id}"
    try:
        used = await redis.incr(key)
        if used == 1:
            await redis.expire(key, window_sec)
        ttl = await redis.ttl(key)
    except Exception:
        log.exception("Redis rate limit check failed; allowing request")
        return RateLimitResult(allowed=True, remaining=limit, limit=limit, retry_after_sec=0)

    if used > limit:
        return RateLimitResult(
            allowed=False,
            remaining=0,
            limit=limit,
            retry_after_sec=max(int(ttl), 1),
        )
    return RateLimitResult(
        allowed=True,
        remaining=max(0, limit - int(used)),
        limit=limit,
        retry_after_sec=max(int(ttl), 0),
    )


async def check_text(telegram_id: int) -> RateLimitResult:
    return await _check(telegram_id, "text", RateLimits.TEXT_PER_HOUR, 3600)


async def check_photo(telegram_id: int) -> RateLimitResult:
    return await _check(telegram_id, "photo", RateLimits.PHOTO_PER_10_MIN, 600)


async def check_product(telegram_id: int) -> RateLimitResult:
    return await _check(telegram_id, "product", RateLimits.PRODUCT_PER_HOUR, 3600)


def humanize_retry(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        return f"{seconds // 60} мин"
    return f"{seconds // 3600} ч"

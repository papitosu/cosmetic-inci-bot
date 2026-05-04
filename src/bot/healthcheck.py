"""Liveness probe for the bot container.

A real probe needs to confirm that the process can talk to its
backing services. We exercise:
    * Postgres — `SELECT 1` through the configured async engine.
    * Redis — `PING` if `REDIS_URL` is set (best effort).

Exits with non-zero status on any unrecoverable failure so Docker /
Compose can restart the container.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text


async def _check_db() -> None:
    from src.core.db import session_scope

    async with session_scope() as session:
        await session.execute(text("SELECT 1"))


async def _check_redis() -> None:
    from src.core.config import get_settings

    redis_url = get_settings().redis_url
    if not redis_url:
        return
    from redis import asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
    finally:
        await client.close()


async def main() -> int:
    try:
        await _check_db()
    except Exception as exc:
        print(f"db: FAIL {exc!r}", file=sys.stderr)
        return 1
    try:
        await _check_redis()
    except Exception as exc:
        print(f"redis: WARN {exc!r}", file=sys.stderr)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

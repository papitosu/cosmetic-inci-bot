from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.bot.middlewares import rate_limit as rl


@pytest.fixture(autouse=True)
def _reset_singleton():
    rl._redis_singleton = None
    yield
    rl._redis_singleton = None


@pytest.mark.asyncio
async def test_rate_limit_allows_when_redis_unavailable() -> None:
    with patch.object(rl, "_redis", return_value=None):
        result = await rl.check_text(telegram_id=1)
    assert result.allowed is True


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_quota_exceeded() -> None:
    fake = AsyncMock()
    fake.incr = AsyncMock(return_value=999)
    fake.expire = AsyncMock(return_value=True)
    fake.ttl = AsyncMock(return_value=120)

    with patch.object(rl, "_redis", return_value=fake):
        result = await rl.check_photo(telegram_id=42)

    assert result.allowed is False
    assert result.retry_after_sec == 120
    assert result.remaining == 0


@pytest.mark.asyncio
async def test_rate_limit_first_call_sets_expiry() -> None:
    fake = AsyncMock()
    fake.incr = AsyncMock(return_value=1)
    fake.expire = AsyncMock(return_value=True)
    fake.ttl = AsyncMock(return_value=3600)

    with patch.object(rl, "_redis", return_value=fake):
        result = await rl.check_text(telegram_id=42)

    fake.expire.assert_awaited_once()
    assert result.allowed is True


@pytest.mark.asyncio
async def test_rate_limit_handles_redis_exceptions() -> None:
    fake = AsyncMock()
    fake.incr = AsyncMock(side_effect=RuntimeError("boom"))

    with patch.object(rl, "_redis", return_value=fake):
        result = await rl.check_product(telegram_id=42)

    assert result.allowed is True


def test_humanize_retry_seconds() -> None:
    assert rl.humanize_retry(30) == "30 сек"
    assert rl.humanize_retry(120) == "2 мин"
    assert rl.humanize_retry(3700) == "1 ч"

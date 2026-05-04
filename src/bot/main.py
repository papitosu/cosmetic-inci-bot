from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.core.config import get_settings
from src.services.ingredients_db import get_db

log = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_dispatcher() -> Dispatcher:
    from src.bot.handlers import analyze, photo, product, profile, start

    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(product.router)
    dp.include_router(photo.router)
    dp.include_router(analyze.router)
    return dp


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info("Loading ingredients DB ...")
    db = get_db()
    log.info("Ingredients DB loaded: %d canonical names", db.size)

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    log.info("Starting bot polling ...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

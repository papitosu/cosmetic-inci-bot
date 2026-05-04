from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from src.bot.formatting import format_analysis
from src.bot.keyboards import main_menu_kb
from src.bot.media import answer_menu_image
from src.bot.middlewares.rate_limit import check_text, humanize_retry
from src.core.config import get_settings
from src.core.db import session_scope
from src.core.enums import AnalysisSource, SkinType
from src.core.repositories import get_or_create_user, save_analysis
from src.services.analyzer import analyze_full
from src.services.parser import parse

log = logging.getLogger(__name__)
router = Router()


HINT_TEXT = (
    "📝 Пришли мне состав в виде текста (через запятую).\n\n"
    "Пример: <code>Water, Glycerin, Niacinamide, Coconut Oil, Tocopherol, Parfum</code>"
)
HINT_PHOTO = "📸 Пришли фото этикетки с составом."

MENU_BUTTONS = {
    "📝 Анализ по тексту",
    "📸 Анализ по фото",
    "🔎 Поиск по названию",
    "👤 Профиль",
    "ℹ️ Помощь",
}


@router.message(F.text == "📝 Анализ по тексту")
async def hint_text(message: Message) -> None:
    await answer_menu_image(message, "text", HINT_TEXT)


@router.message(F.text == "📸 Анализ по фото")
async def hint_photo(message: Message) -> None:
    await answer_menu_image(message, "photo", HINT_PHOTO)


@router.message(F.text & ~F.text.startswith("/"))
async def analyze_text(message: Message) -> None:
    if message.from_user is None or message.text is None:
        return

    text = message.text.strip()
    if text in MENU_BUTTONS:
        return

    parsed = parse(text)
    if len(parsed) < 2:
        await message.answer(HINT_TEXT)
        return

    rl = await check_text(message.from_user.id)
    if not rl.allowed:
        await message.answer(
            f"⏳ Слишком быстро. Попробуй ещё раз через {humanize_retry(rl.retry_after_sec)}.\n"
            f"<i>Ограничение защищает бот от перегрузки. Лимит {rl.limit} текстов/час.</i>"
        )
        return

    await message.bot.send_chat_action(message.chat.id, "typing")  # type: ignore[union-attr]

    skin_type = SkinType.UNKNOWN
    async with session_scope() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        skin_type = user.skin_type

    settings = get_settings()
    result = await analyze_full(parsed, skin_type, redis_url=settings.redis_url)

    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        await save_analysis(
            session,
            user_id=user.id,
            source=AnalysisSource.TEXT,
            raw_input=text,
            ingredients=[i.canonical or i.raw for i in result.ingredients],
            result_payload=result.to_dict(),
            risk_score=float(result.risk_score),
        )

    await message.answer(
        format_analysis(result),
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )

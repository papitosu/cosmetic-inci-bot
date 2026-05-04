from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from src.bot.formatting import format_analysis
from src.bot.keyboards import main_menu_kb
from src.bot.middlewares.rate_limit import check_photo, humanize_retry
from src.core.config import get_settings
from src.core.db import session_scope
from src.core.enums import AnalysisSource, SkinType
from src.core.repositories import get_or_create_user, save_analysis
from src.services.analyzer import analyze_full
from src.services.ocr import extract_ingredients_from_image
from src.services.parser import parse

log = logging.getLogger(__name__)
router = Router()


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    if message.from_user is None or not message.photo:
        return

    rl = await check_photo(message.from_user.id)
    if not rl.allowed:
        await message.answer(
            f"⏳ Слишком много фото подряд. Попробуй через {humanize_retry(rl.retry_after_sec)}.\n"
            f"<i>OCR — тяжёлая операция, лимит {rl.limit} фото / 10 мин.</i>"
        )
        return

    await message.bot.send_chat_action(message.chat.id, "typing")  # type: ignore[union-attr]

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)  # type: ignore[union-attr]
    if file.file_path is None:
        await message.answer("Не удалось скачать фото, попробуйте ещё раз.")
        return

    buf = await message.bot.download_file(file.file_path)  # type: ignore[union-attr]
    if buf is None:
        await message.answer("Не удалось загрузить фото.")
        return

    image_bytes = buf.read()
    try:
        extracted_text = await extract_ingredients_from_image(image_bytes)
    except Exception:
        log.exception("OCR failed")
        await message.answer(
            "Не удалось распознать текст на фото. Попробуйте более чёткое фото."
        )
        return

    parsed = parse(extracted_text)
    if len(parsed) < 2:
        await message.answer(
            "На фото не удалось разобрать список ингредиентов. "
            "Попробуйте снимок ближе и при равномерном освещении."
        )
        return

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
            source=AnalysisSource.PHOTO,
            raw_input=extracted_text,
            ingredients=[i.canonical or i.raw for i in result.ingredients],
            result_payload=result.to_dict(),
            risk_score=float(result.risk_score),
        )

    snippet = extracted_text.strip().replace("<", "&lt;").replace(">", "&gt;")
    if len(snippet) > 300:
        snippet = snippet[:300] + "…"
    await message.answer(
        f"📸 <b>Распознанный текст:</b>\n<code>{snippet}</code>",
        parse_mode="HTML",
    )
    await message.answer(format_analysis(result), parse_mode="HTML", reply_markup=main_menu_kb())

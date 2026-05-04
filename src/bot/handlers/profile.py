from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import SKIN_TYPE_LABELS, history_kb, skin_type_kb
from src.bot.media import answer_menu_image
from src.core.db import session_scope
from src.core.repositories import get_or_create_user, list_user_analyses

router = Router()
PAGE_SIZE = 5


@router.message(Command("profile"))
@router.message(F.text == "👤 Профиль")
async def cmd_profile(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_scope() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        analyses = await list_user_analyses(session, user.id, limit=PAGE_SIZE, offset=0)
        analyses_count = len(analyses)
        skin_type = user.skin_type

    label = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else str(message.from_user.id)
    )
    skin_label = SKIN_TYPE_LABELS.get(skin_type, skin_type.value)

    text = (
        "👤 <b>Профиль</b>\n"
        f"Telegram: <code>{label}</code>\n"
        f"Тип кожи: <b>{skin_label}</b>"
    )
    await answer_menu_image(message, "profile", text)

    if analyses_count:
        history = "🗂 <b>Последние анализы:</b>\n" + "\n".join(
            _format_history_row(a) for a in analyses
        )
        has_next = analyses_count == PAGE_SIZE
        await message.answer(history, reply_markup=history_kb(0, has_next))
    else:
        await message.answer(
            "У тебя пока нет анализов. Пришли состав текстом, фото или название продукта."
        )

    await message.answer("Сменить тип кожи:", reply_markup=skin_type_kb())


@router.callback_query(F.data.startswith("hist:"))
async def cb_history(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None or callback.message is None:
        return
    try:
        offset = int(callback.data.removeprefix("hist:"))
    except ValueError:
        return

    async with session_scope() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
        )
        analyses = await list_user_analyses(
            session, user.id, limit=PAGE_SIZE, offset=offset
        )

    if not analyses and offset == 0:
        await callback.answer("История пуста")
        return
    if not analyses:
        await callback.answer("Дальше нет")
        return

    history = "🗂 <b>Анализы:</b>\n" + "\n".join(_format_history_row(a) for a in analyses)
    has_next = len(analyses) == PAGE_SIZE
    await callback.message.edit_text(
        history, reply_markup=history_kb(offset, has_next)
    )
    await callback.answer()


def _format_history_row(a) -> str:
    when = (a.created_at or datetime.now(timezone.utc)).strftime("%d.%m %H:%M")
    risk = float(a.risk_score) if a.risk_score is not None else 0.0
    title = a.product_title or _short(a.raw_input or "—")
    src_emoji = {"text": "📝", "photo": "📸", "product": "🔎"}.get(a.source.value, "•")
    return f"{src_emoji} <i>{when}</i> · риск {risk:.0f}/100 · {title}"


def _short(s: str, maxlen: int = 40) -> str:
    s = s.strip().replace("\n", " ")
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"

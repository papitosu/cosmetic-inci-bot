from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import SKIN_TYPE_LABELS, main_menu_kb, skin_type_kb
from src.bot.media import answer_menu_image
from src.core.db import session_scope
from src.core.enums import SkinType
from src.core.repositories import get_or_create_user

router = Router()


WELCOME = (
    "👋 Привет! Я помогу разобрать состав косметики (INCI). Бесплатно и без лимитов.\n\n"
    "Что я умею:\n"
    "• Анализирую состав по тексту, фото или названию продукта\n"
    "• Нахожу комедогены, аллергены, раздражители и полезные активы\n"
    "• Подтягиваю данные из EU CosIng и PubChem (NIH)\n"
    "• Учитываю твой тип кожи\n"
    "• Подсказываю аналоги почище через Open Beauty Facts\n\n"
    "Сначала укажи тип кожи — это влияет на анализ."
)

HELP = (
    "ℹ️ <b>Как пользоваться:</b>\n\n"
    "• «📝 Анализ по тексту» — пришли список ингредиентов через запятую\n"
    "• «📸 Анализ по фото» — пришли фото этикетки с составом\n"
    "• «🔎 Поиск по названию» — введи название продукта\n"
    "• «👤 Профиль» — тип кожи и история анализов\n\n"
    "Команды: /start /profile /help\n\n"
    "<i>Бот использует открытые данные: BEAUTEE INCI, EU CosIng, PubChem (NIH), Open Beauty Facts.</i>"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_scope() as session:
        await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    await message.answer(WELCOME, reply_markup=skin_type_kb())
    await message.answer("Главное меню:", reply_markup=main_menu_kb())


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message) -> None:
    await answer_menu_image(message, "help", HELP)


@router.callback_query(F.data.startswith("skin:"))
async def on_skin_chosen(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        return
    skin_value = callback.data.removeprefix("skin:")
    try:
        skin_type = SkinType(skin_value)
    except ValueError:
        await callback.answer("Неизвестный тип")
        return

    async with session_scope() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name,
        )
        user.skin_type = skin_type

    label = SKIN_TYPE_LABELS.get(skin_type, skin_type.value)
    await callback.answer(f"Тип кожи: {label}")
    if callback.message is not None:
        await callback.message.edit_text(
            f"✅ Тип кожи сохранён: <b>{label}</b>\n\n"
            "Теперь выбери действие в меню или просто пришли состав текстом."
        )

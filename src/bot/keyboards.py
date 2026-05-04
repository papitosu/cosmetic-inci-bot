from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from src.core.enums import SkinType

SKIN_TYPE_LABELS: dict[SkinType, str] = {
    SkinType.NORMAL: "Нормальная",
    SkinType.DRY: "Сухая",
    SkinType.OILY: "Жирная",
    SkinType.COMBINATION: "Комбинированная",
    SkinType.SENSITIVE: "Чувствительная",
    SkinType.ACNE_PRONE: "С акне / склонная к высыпаниям",
}


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Анализ по тексту"), KeyboardButton(text="📸 Анализ по фото")],
            [KeyboardButton(text="🔎 Поиск по названию"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


def skin_type_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    items = list(SKIN_TYPE_LABELS.items())
    for i in range(0, len(items), 2):
        chunk = items[i : i + 2]
        rows.append(
            [
                InlineKeyboardButton(text=label, callback_data=f"skin:{st.value}")
                for st, label in chunk
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_kb(offset: int, has_next: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        prev_offset = max(0, offset - 5)
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"hist:{prev_offset}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"hist:{offset + 5}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_choice_kb(products: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """`products` is a list of (id, label) pairs."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"prod:{pid}")]
        for pid, label in products
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="prod:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def analogs_kb(product_idx: int) -> InlineKeyboardMarkup:
    """Inline button shown after a product analysis to suggest cleaner analogs."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Найти аналог почище",
                    callback_data=f"analog:{product_idx}",
                )
            ]
        ]
    )

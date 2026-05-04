from __future__ import annotations

from pathlib import Path
from typing import Any

from aiogram.types import FSInputFile, Message

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"

MENU_IMAGES = {
    "help": _ASSETS_DIR / "menu_help.png",
    "text": _ASSETS_DIR / "menu_text.png",
    "photo": _ASSETS_DIR / "menu_photo.png",
    "profile": _ASSETS_DIR / "menu_profile.png",
    "search": _ASSETS_DIR / "menu_search.png",
}


async def answer_menu_image(
    message: Message,
    image_key: str,
    caption: str,
    **kwargs: Any,
) -> None:
    """Send a branded menu image with a text caption.

    If the image is missing in a future deploy, keep the bot usable by
    falling back to a regular text message.
    """
    path = MENU_IMAGES.get(image_key)
    if path and path.exists():
        await message.answer_photo(FSInputFile(path), caption=caption, **kwargs)
        return
    await message.answer(caption, **kwargs)

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot.formatting import format_analysis
from src.bot.keyboards import analogs_kb, main_menu_kb, product_choice_kb
from src.bot.media import answer_menu_image
from src.bot.middlewares.rate_limit import check_product, humanize_retry
from src.bot.states import ProductSearch
from src.core.config import get_settings
from src.core.db import session_scope
from src.core.enums import AnalysisSource, SkinType
from src.core.repositories import get_or_create_user, save_analysis
from src.services.analogs import find_analogs, format_analogs
from src.services.analyzer import analyze_full
from src.services.local_products import LocalProduct, get_catalog
from src.services.parser import parse
from src.services.product_search import (
    OpenBeautyFactsClient,
    OpenBeautyFactsProduct,
)

ProductCandidate = OpenBeautyFactsProduct | LocalProduct

# Below this many local hits we still hit Open Beauty Facts so the
# user gets at least ~6-8 options to pick from. Above it, the local
# catalogue is dense enough on its own and we save the network round
# trip (and OBF rate-limit budget) — most popular Sephora queries fall
# in this regime.
LOCAL_HIT_THRESHOLD = 4

# Tokens shorter than this carry no signal (`the`, `de`, `la`, …) and
# would let almost any product pass the relevance filter.
_MIN_TOKEN_LEN = 3
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

log = logging.getLogger(__name__)
router = Router()


def _query_tokens(query: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(query.lower()) if len(t) >= _MIN_TOKEN_LEN]


def _has_label(p: ProductCandidate) -> bool:
    """Reject candidates the user could not meaningfully tap on.

    OBF rows occasionally come back with empty ``brands`` *and*
    ``product_name`` — they survive ``brand_and_name`` only as the dash
    placeholder, which renders as a blank button. Drop them upstream."""
    label = (p.brand_and_name() or "").strip()
    return bool(label) and label != "—"


def _is_relevant(p: OpenBeautyFactsProduct, q_tokens: list[str]) -> bool:
    """OBF's ``search_terms`` matches the full profile, including
    ``ingredients_text``, so a query like ``"The Ordinary Niacinamide"``
    pulls every popular product that merely *contains* niacinamide
    (baby soaps included). Require at least one query token to land in
    the brand or product name itself; that's how a human would judge
    "did this search actually find what I asked for"."""
    if not q_tokens:
        return True
    haystack = f"{p.brands or ''} {p.product_name or ''}".lower()
    return any(tok in haystack for tok in q_tokens)


@router.message(F.text == "🔎 Поиск по названию")
async def start_product_search(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    rl = await check_product(message.from_user.id)
    if not rl.allowed:
        await message.answer(
            f"⏳ Слишком много поисков подряд. Попробуй через {humanize_retry(rl.retry_after_sec)}.\n"
            f"<i>Лимит {rl.limit} поисков/час.</i>"
        )
        return

    await state.set_state(ProductSearch.waiting_for_query)
    await answer_menu_image(
        message,
        "search",
        "🔎 Введите название продукта (например, <i>The Ordinary Niacinamide</i>):"
    )


@router.message(ProductSearch.waiting_for_query, F.text)
async def handle_query(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    query = message.text.strip()
    if len(query) < 3:
        await message.answer("Слишком короткий запрос. Попробуйте ещё раз.")
        return

    await message.bot.send_chat_action(message.chat.id, "typing")  # type: ignore[union-attr]
    catalog = get_catalog()
    local_hits: list[LocalProduct] = catalog.search(query, limit=6)

    obf_hits: list[OpenBeautyFactsProduct] = []
    if len(local_hits) < LOCAL_HIT_THRESHOLD:
        async with OpenBeautyFactsClient() as client:
            obf_raw = await client.search(query, page_size=12)
        q_tokens = _query_tokens(query)
        obf_hits = [
            p for p in obf_raw
            if p.ingredients_text and _has_label(p) and _is_relevant(p, q_tokens)
        ]
        dropped = len(obf_raw) - len(obf_hits)
        if dropped:
            log.info("OBF post-filter dropped %d/%d for %r", dropped, len(obf_raw), query)

    seen_titles: set[str] = set()
    candidates: list[ProductCandidate] = []
    for p in local_hits:
        if not _has_label(p):
            continue
        title = p.brand_and_name().lower()
        if title in seen_titles:
            continue
        seen_titles.add(title)
        candidates.append(p)
    for p in obf_hits:
        title = p.brand_and_name().lower()
        if title in seen_titles:
            continue
        seen_titles.add(title)
        candidates.append(p)

    if not candidates:
        await message.answer(
            "Не нашёл продукт с таким названием.\n"
            "Локальная база — это премиум-каталог Sephora; масс-маркет (The Ordinary, "
            "La Roche-Posay, CeraVe и т.п.) ищется в Open Beauty Facts.\n"
            "Попробуйте указать бренд + ключевое слово (например, <i>Cerave SA Cleanser</i>) "
            "или пришлите состав текстом / фото."
        )
        await state.clear()
        return

    options: list[tuple[str, str]] = []
    cache: dict[str, ProductCandidate] = {}
    for idx, p in enumerate(candidates[:8]):
        label = p.brand_and_name()[:60]
        pid = str(idx)
        options.append((pid, label))
        cache[pid] = p

    await state.update_data(products=cache)
    await state.set_state(ProductSearch.choosing_product)
    src_summary: list[str] = []
    if local_hits:
        src_summary.append(f"локальный каталог Sephora — {len(local_hits)}")
    if obf_hits:
        src_summary.append(f"Open Beauty Facts — {len(obf_hits)}")
    src_line = " · ".join(src_summary) if src_summary else ""
    body = f"Нашёл {len(cache)} продуктов с составом — выбери:"
    if src_line:
        body += f"\n<i>{src_line}</i>"
    await message.answer(body, reply_markup=product_choice_kb(options))


@router.callback_query(F.data.startswith("prod:"), ProductSearch.choosing_product)
async def handle_product_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None or callback.from_user is None:
        return
    payload = callback.data.removeprefix("prod:")
    if payload == "cancel":
        await state.clear()
        await callback.answer("Отменено")
        if callback.message is not None:
            await callback.message.edit_text("Поиск отменён.")
        return

    data = await state.get_data()
    cache: dict[str, ProductCandidate] = data.get("products") or {}
    product = cache.get(payload)
    if product is None or not product.ingredients_text:
        await callback.answer("Ошибка выбора, попробуйте снова")
        return

    parsed = parse(product.ingredients_text)
    if len(parsed) < 2:
        await callback.answer("Состав пуст")
        await state.clear()
        return

    skin_type = SkinType.UNKNOWN
    async with session_scope() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name,
        )
        skin_type = user.skin_type

    settings = get_settings()
    result = await analyze_full(parsed, skin_type, redis_url=settings.redis_url)

    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        await save_analysis(
            session,
            user_id=user.id,
            source=AnalysisSource.PRODUCT,
            raw_input=product.ingredients_text,
            product_title=product.brand_and_name(),
            ingredients=[i.canonical or i.raw for i in result.ingredients],
            result_payload=result.to_dict(),
            risk_score=float(result.risk_score),
        )

    await state.update_data(
        last_product=product,
        last_risk=float(result.risk_score),
    )
    await state.set_state(ProductSearch.viewing_result)
    await callback.answer()
    if callback.message is not None:
        kb = analogs_kb(0) if product.primary_category() else None
        await callback.message.edit_text(
            format_analysis(result, product_title=product.brand_and_name()),
            reply_markup=kb,
        )
        await callback.message.answer("Главное меню:", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("analog:"))
async def handle_find_analogs(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        return

    data = await state.get_data()
    product: ProductCandidate | None = data.get("last_product")
    risk = data.get("last_risk")

    if not product or risk is None:
        await callback.answer("Сначала выбери продукт")
        return

    await callback.answer("Ищу аналоги…")

    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        skin_type = user.skin_type

    try:
        analogs = await find_analogs(product, float(risk), skin_type)
    except Exception:
        log.exception("Analog search failed")
        if callback.message is not None:
            await callback.message.answer(
                "Не удалось получить аналоги — попробуй позже."
            )
        return

    text = format_analogs(analogs)
    if callback.message is not None:
        await callback.message.answer(text, parse_mode="HTML")

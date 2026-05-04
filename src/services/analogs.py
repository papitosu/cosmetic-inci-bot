"""Find cleaner analogs of a given product.

Source product can come from either Open Beauty Facts or our local
Sephora catalogue — both expose the same OBF-shaped category tag, so
the strategy is identical:

    1. Take the source product's most specific category (``en:...``).
    2. Fetch ~30 popular products in the same category from OBF.
    3. Run a lightweight (no external enrichment) `analyze` on each.
    4. Return up to N candidates with strictly lower risk_score than the
       source, sorted by ascending risk.

This is a free feature available to every user — OBF data is public and
ODbL-licensed, and the per-candidate analysis uses only the in-memory
INCI dictionary (no extra HTTP calls).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from src.core.enums import SkinType
from src.services.analyzer import analyze
from src.services.local_products import LocalProduct
from src.services.parser import parse
from src.services.product_search import (
    OpenBeautyFactsClient,
    OpenBeautyFactsProduct,
)

log = logging.getLogger(__name__)

ProductLike = OpenBeautyFactsProduct | LocalProduct


@dataclass
class AnalogCandidate:
    product: OpenBeautyFactsProduct
    risk_score: float
    verdict: str


async def find_analogs(
    source: ProductLike,
    source_risk_score: float,
    skin_type: SkinType,
    *,
    limit: int = 3,
    pool_size: int = 30,
    min_improvement: float = 5.0,
) -> list[AnalogCandidate]:
    category = source.primary_category()
    if not category:
        return []

    async with OpenBeautyFactsClient() as client:
        pool = await client.search_by_category(category, page_size=pool_size)

    same_code = getattr(source, "code", None)
    candidates: list[AnalogCandidate] = []
    for p in pool:
        if not p.ingredients_text:
            continue
        if same_code and p.code == same_code:
            continue
        parsed = parse(p.ingredients_text)
        if len(parsed) < 3:
            continue
        try:
            result = await asyncio.to_thread(analyze, parsed, skin_type)
        except Exception:
            log.exception("Analog scoring failed for %s", p.code)
            continue
        if result.risk_score >= source_risk_score - min_improvement:
            continue
        candidates.append(
            AnalogCandidate(
                product=p,
                risk_score=float(result.risk_score),
                verdict=result.verdict,
            )
        )

    candidates.sort(key=lambda c: c.risk_score)
    return candidates[:limit]


def format_analogs(candidates: list[AnalogCandidate]) -> str:
    if not candidates:
        return (
            "🔄 <b>Аналоги</b>\n"
            "Не нашёл в Open Beauty Facts продукта с заметно более чистым "
            "составом в той же категории."
        )
    lines = ["🔄 <b>Аналоги почище:</b>"]
    emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}
    for c in candidates:
        title = c.product.brand_and_name()
        e = emoji.get(c.verdict, "•")
        lines.append(f"{e} <b>{title}</b> — риск {c.risk_score:.0f}/100")
    lines.append("")
    lines.append("<i>Источник: Open Beauty Facts (ODbL).</i>")
    return "\n".join(lines)

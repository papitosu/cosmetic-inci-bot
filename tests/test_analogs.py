from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.enums import SkinType
from src.services.analogs import AnalogCandidate, find_analogs, format_analogs
from src.services.product_search import OpenBeautyFactsProduct


def _product(
    name: str,
    code: str,
    ingredients: str,
    category: str = "en:face-creams",
) -> OpenBeautyFactsProduct:
    return OpenBeautyFactsProduct(
        code=code,
        product_name=name,
        brands="Brand",
        ingredients_text=ingredients,
        image_url=None,
        categories_tags=["en:cosmetics", category],
    )


def test_primary_category_picks_specific_tag() -> None:
    p = _product("X", "1", "Water", category="en:moisturizing-creams")
    assert p.primary_category() == "en:moisturizing-creams"


def test_primary_category_returns_none_when_empty() -> None:
    p = _product("X", "1", "Water")
    p.categories_tags = []
    assert p.primary_category() is None


def _patch_obf(pool: list[OpenBeautyFactsProduct]):
    fake_client = MagicMock()
    fake_client.search_by_category = AsyncMock(return_value=pool)

    @asynccontextmanager
    async def fake_ctx(*args, **kwargs):
        yield fake_client

    return patch("src.services.analogs.OpenBeautyFactsClient", fake_ctx)


@pytest.mark.asyncio
async def test_find_analogs_drops_source_and_worse_options() -> None:
    src = _product(
        "Source",
        "src",
        "Water, Coconut Oil, Isopropyl Myristate, Parfum",
    )
    pool = [
        _product("Clean", "p1", "Water, Glycerin, Niacinamide, Panthenol"),
        _product("Same as source", "src", "Water, Coconut Oil"),
    ]

    with _patch_obf(pool):
        results = await find_analogs(
            src,
            source_risk_score=60.0,
            skin_type=SkinType.NORMAL,
            limit=5,
            min_improvement=1.0,
        )

    codes = {c.product.code for c in results}
    assert "src" not in codes
    for c in results:
        assert c.risk_score < 60.0


@pytest.mark.asyncio
async def test_find_analogs_returns_empty_when_no_category() -> None:
    src = _product("X", "1", "Water, Glycerin")
    src.categories_tags = []
    results = await find_analogs(src, 50.0, SkinType.NORMAL)
    assert results == []


def test_format_analogs_handles_empty() -> None:
    text = format_analogs([])
    assert "Не нашёл" in text


def test_format_analogs_lists_items() -> None:
    p = _product("Clean Cream", "p1", "Water")
    text = format_analogs(
        [AnalogCandidate(product=p, risk_score=10.0, verdict="low")]
    )
    assert "Clean Cream" in text
    assert "10" in text

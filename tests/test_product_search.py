"""Unit tests for OBF post-filtering and label hygiene.

The product search handler does the actual filtering, but the helpers
are pure and easy to test on their own — that's where the logic lives."""
from __future__ import annotations

from src.bot.handlers.product import (
    _has_label,
    _is_relevant,
    _query_tokens,
)
from src.services.product_search import OpenBeautyFactsProduct


def _obf(brands: str | None, name: str | None, ingredients: str = "Aqua") -> OpenBeautyFactsProduct:
    return OpenBeautyFactsProduct(
        code="000",
        product_name=name,
        brands=brands,
        ingredients_text=ingredients,
        image_url=None,
        categories_tags=[],
    )


def test_query_tokens_drops_short_words() -> None:
    assert _query_tokens("The Ordinary Niacinamide") == ["the", "ordinary", "niacinamide"]
    # min-length floor drops "de"/"la" without keeping a stop-words list.
    assert _query_tokens("crème de la mer") == ["crème", "mer"]
    assert _query_tokens("a") == []
    # Cyrillic and digits.
    assert _query_tokens("сыворотка с цинком 10%") == ["сыворотка", "цинком"]


def test_brand_and_name_returns_dash_for_blank_obf_row() -> None:
    """OBF rows with whitespace/comma values must collapse to the dash
    sentinel so callers can detect 'no useful label'."""
    assert _obf(brands="", name="").brand_and_name() == "—"
    assert _obf(brands=",", name="").brand_and_name() == "—"
    assert _obf(brands="   ", name="   ").brand_and_name() == "—"
    # Real labels still join cleanly.
    assert _obf(brands="The Ordinary", name="Niacinamide 10%").brand_and_name() == (
        "The Ordinary — Niacinamide 10%"
    )
    # Brand only, no name.
    assert _obf(brands="Cadum", name=None).brand_and_name() == "Cadum"


def test_has_label_rejects_blank_buttons() -> None:
    assert _has_label(_obf("The Ordinary", "Niacinamide 10%"))
    assert not _has_label(_obf("", ""))
    assert not _has_label(_obf(",", None))


def test_relevance_rejects_baby_soap_for_niacinamide_query() -> None:
    """Reproduces the production bug: niacinamide is in *ingredients*
    of MIXA bébé / Cadum, but the user asked for The Ordinary Niacinamide.
    The brand+name does not contain any query token, so it must drop."""
    q = _query_tokens("The Ordinary Niacinamide")
    baby = _obf("MIXA", "MIXA bébé crème", ingredients="Aqua, Niacinamide, Glycerin")
    cadum = _obf("Cadum", "Lait hydratant", ingredients="Aqua, Niacinamide")
    assert not _is_relevant(baby, q)
    assert not _is_relevant(cadum, q)


def test_relevance_keeps_real_match() -> None:
    q = _query_tokens("The Ordinary Niacinamide")
    target = _obf("The Ordinary", "Niacinamide 10% + Zinc 1%")
    assert _is_relevant(target, q)
    # Single significant token also enough.
    assert _is_relevant(_obf("Some Brand", "Niacinamide Serum"), q)


def test_relevance_passes_when_query_is_empty() -> None:
    """Empty query → don't drop everything; that's the caller's check."""
    assert _is_relevant(_obf("X", "Y"), [])
